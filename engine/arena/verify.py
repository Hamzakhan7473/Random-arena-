from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from arena.errors import ActualConflict, ArenaError, ForecastFormat, ManifestError
from arena.manifest import parse_manifest_line, read_manifest, sha256_file
from arena.paths import FORECAST_REL, manifest_path
from arena.schema import Forecast, Observation
from arena.store import forecast_files, load_forecast_file
from arena.timeutil import as_utc_hour, parse_utc


@dataclass(frozen=True)
class VerifyReport:
    errors: tuple[str, ...]
    files_checked: int

    @property
    def ok(self) -> bool:
        return not self.errors


def verify(root: Path, *, check_git: bool = True) -> VerifyReport:
    errors: list[str] = []
    try:
        recorded = read_manifest(manifest_path(root))
    except ManifestError as exc:
        return VerifyReport(errors=(str(exc),), files_checked=0)

    sealed: dict[str, str] = {}
    for _issue_time, rel, digest in recorded:
        if rel in sealed:
            errors.append(f"manifest lists {rel} more than once")
            continue
        if FORECAST_REL.fullmatch(rel) is None:
            errors.append(f"manifest path is not a forecast file: {rel}")
        sealed[rel] = digest

    on_disk: list[str] = []
    parsed: dict[str, list[Forecast]] = {}
    for path in forecast_files(root):
        rel = path.relative_to(root).as_posix()
        on_disk.append(rel)
        digest = sha256_file(path)
        recorded_digest = sealed.get(rel)
        if recorded_digest is None:
            errors.append(f"{rel} is not sealed in the manifest")
        elif recorded_digest != digest:
            errors.append(f"{rel} hash does not match the manifest")
        try:
            parsed[rel] = load_forecast_file(root, path)
        except (ForecastFormat, ManifestError) as exc:
            errors.append(str(exc))
            parsed[rel] = []

    for rel in sealed:
        if rel not in on_disk:
            errors.append(f"manifest entry is missing on disk: {rel}")

    if check_git and on_disk:
        probe = _git(root, "rev-parse", "--git-dir")
        if probe.returncode != 0:
            errors.append("forecasts exist but this directory is not a git repository")
        else:
            for rel in on_disk:
                errors.extend(_verify_git_file(root, rel, sealed.get(rel), parsed.get(rel, [])))

    return VerifyReport(errors=tuple(_unique(errors)), files_checked=len(on_disk))


def _verify_git_file(
    root: Path,
    rel: str,
    digest: str | None,
    rows: list[Forecast],
) -> list[str]:
    errors: list[str] = []
    status = _git(root, "status", "--porcelain", "--", rel)
    if status.returncode != 0:
        return [f"git status failed for {rel}"]
    if status.stdout.strip():
        return [f"{rel} has uncommitted changes"]

    log = _git(root, "log", "--first-parent", "--format=%cI%x09%H", "--", rel)
    if log.returncode != 0:
        return [f"git log failed for {rel}"]
    commits = [line for line in log.stdout.splitlines() if line.strip()]
    if len(commits) != 1:
        return [f"{rel} must appear in exactly one commit, found {len(commits)}"]
    committed_raw, commit = commits[0].split("\t", 1)
    committed_at = parse_utc(committed_raw)
    shown = _git(root, "show", f"{commit}:data/manifest/hashes.log")
    if shown.returncode != 0:
        errors.append(f"{rel} introducing commit has no manifest")
    else:
        published = _digest_in_manifest_text(shown.stdout, rel)
        if published is None:
            errors.append(f"{rel} hash was not published in the commit that added the file")
        elif digest is not None and published != digest:
            errors.append(f"{rel} hash in its introducing commit does not match the file")
    if not rows:
        return errors
    for row in rows:
        if committed_at >= row.target_time:
            errors.append(
                f"{rel} was committed at {committed_at.strftime('%Y-%m-%dT%H:%M:%SZ')}, "
                f"which is not before {row.model} {row.horizon}h target "
                f"{row.target_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )

    parent = _git(root, "rev-parse", "--verify", f"{commit}^")
    if parent.returncode != 0:
        errors.append(f"{rel} introducing commit has no parent, so earlier prices cannot be audited")
        return errors
    try:
        actuals = _actuals_at_rev(root, parent.stdout.strip())
    except ArenaError as exc:
        errors.append(f"{rel} parent actuals are unreadable: {exc}")
        return errors

    by_symbol: dict[str, dict[datetime, float]] = {}
    for observation in actuals:
        by_symbol.setdefault(observation.symbol, {})[observation.time] = observation.close
    issued_at = rows[0].issue_time
    for symbol in sorted({row.product for row in rows}):
        series = by_symbol.get(symbol, {})
        if issued_at not in series:
            errors.append(f"{rel} parent commit has no {symbol} close at {issued_at.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        if any(moment > issued_at for moment in series):
            errors.append(f"{rel} parent commit contains {symbol} prices after the issue hour")
    return errors


def _actuals_at_rev(root: Path, revision: str) -> list[Observation]:
    listing = _git(root, "ls-tree", "-r", "--name-only", revision, "data/actuals")
    if listing.returncode != 0:
        raise ManifestError("could not list actuals in the parent commit")
    observations: list[Observation] = []
    for rel in listing.stdout.splitlines():
        if rel.endswith(".parquet"):
            observations.extend(_observations_from_parquet_rev(root, revision, rel))
        elif rel.endswith(".jsonl"):
            observations.extend(_observations_from_jsonl_rev(root, revision, rel))
    return observations


def _observations_from_parquet_rev(root: Path, revision: str, rel: str) -> list[Observation]:
    blob = subprocess.run(
        ["git", "--no-pager", "show", f"{revision}:{rel}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if blob.returncode != 0:
        raise ManifestError(f"could not read {rel} from the parent commit")
    try:
        candles = pd.read_parquet(io.BytesIO(blob.stdout), engine="pyarrow")
    except Exception as exc:
        raise ActualConflict(f"{rel} in the parent commit is not a valid parquet close") from exc
    product_id = Path(rel).stem
    rows: list[Observation] = []
    for row in candles.itertuples(index=False):
        stamp = pd.Timestamp(row.timestamp_utc)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        else:
            stamp = stamp.tz_convert("UTC")
        known_at = as_utc_hour(stamp.to_pydatetime() + timedelta(hours=1))
        rows.append(Observation(symbol=product_id, time=known_at, close=float(row.close)))
    return rows


def _observations_from_jsonl_rev(root: Path, revision: str, rel: str) -> list[Observation]:
    blob = _git(root, "show", f"{revision}:{rel}")
    if blob.returncode != 0:
        raise ManifestError(f"could not read {rel} from the parent commit")
    observations: list[Observation] = []
    for line in blob.stdout.splitlines():
        if not line.strip():
            continue
        try:
            observations.append(Observation.model_validate_json(line))
        except Exception as exc:
            raise ActualConflict(f"{rel} in the parent commit is not a valid close") from exc
    return observations


def _digest_in_manifest_text(text: str, rel: str) -> str | None:
    found: str | None = None
    for line in text.splitlines():
        parsed = parse_manifest_line(line)
        if parsed is None:
            continue
        _issue_time, recorded, digest = parsed
        if recorded == rel:
            found = digest
    return found


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-pager", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
