from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from arena.actuals import load_observations
from arena.errors import ArenaError, BackfillCutoff, ForecastFormat, ForecastImmutable, InsufficientHistory
from arena.ingest import DEFAULT_PRODUCTS
from arena.models.base import Forecaster
from arena.models.registry import load_forecasters
from arena.paths import (
    RETROSPECTIVE_FORECAST_REL,
    leaderboard_path,
    manifest_path,
    retrospective_forecast_rel,
    retrospective_leaderboard_path,
)
from arena.publish import encode_forecasts
from arena.run_forecasts import code_git_sha, log_price_history, prices_from_log_quantiles
from arena.schema import HORIZON_HOURS, QUANTILES, Forecast, load_forecast_line
from arena.score import attach_retrospective, build_leaderboard, label_retrospective
from arena.timeutil import as_utc_hour, ensure_utc, format_utc

# Default origin is after every published cutoff below. Issue once per UTC day.
DEFAULT_ORIGIN = datetime(2025, 11, 1, tzinfo=timezone.utc)
STEP_HOURS = 24

# Stated training / weight-freeze bounds. `cutoff` is the last calendar day that
# may still be in the published corpus or, when no corpus end date is stated,
# the Hugging Face createdAt date. Backfill issue times must be strictly after
# the latest of these dates.
CUTOFFS: dict[str, dict[str, str | None]] = {
    "no-change": {
        "cutoff": None,
        "source": "none",
        "note": "Online estimator. No pretraining corpus; uses only prices through issue_time.",
    },
    "ewma-drift": {
        "cutoff": None,
        "source": "none",
        "note": "Online EWMA of log returns. No pretraining corpus; uses only prices through issue_time.",
    },
    "ar1": {
        "cutoff": None,
        "source": "none",
        "note": "Online AR(1) on log returns. No pretraining corpus; uses only prices through issue_time.",
    },
    "timesfm-2.5": {
        "cutoff": "2023-11-30",
        "source": "https://huggingface.co/google/timesfm-2.5-200m-pytorch",
        "note": (
            "Model card Data section: Wikimedia Pageviews cutoff Nov 2023; "
            "Google Trends top queries cutoff EoY 2022. GiftEvalPretrain is "
            "listed without a calendar end date."
        ),
    },
    "chronos-bolt-small": {
        "cutoff": "2024-11-25",
        "source": "https://huggingface.co/amazon/chronos-bolt-small",
        "note": (
            "No calendar corpus end date is stated. Card: trained on nearly "
            "100 billion observations; cites Chronos paper "
            "https://arxiv.org/abs/2403.07815. Bound is Hugging Face createdAt "
            "2024-11-25T09:46:10Z."
        ),
    },
    "chronos-2": {
        "cutoff": "2025-10-30",
        "source": "https://huggingface.co/amazon/chronos-2",
        "note": (
            "No calendar corpus end date is stated. Card: Chronos Datasets + "
            "GIFT-Eval Pretrain + synthetic; paper "
            "https://arxiv.org/abs/2510.15821. Bound is Hugging Face createdAt "
            "2025-10-30T14:54:39Z."
        ),
    },
}

@dataclass(frozen=True)
class BackfillResult:
    start: datetime
    end: datetime
    issued: list[datetime]
    skipped: list[tuple[datetime, str]]
    n_forecasts: int
    retrospective_path: Path
    leaderboard_path: Path
    board: dict = field(repr=False)


def parse_cutoff(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def latest_cutoff_entry() -> tuple[str, date, str]:
    latest: tuple[str, date, str] | None = None
    for name, row in CUTOFFS.items():
        day = parse_cutoff(row["cutoff"])
        if day is None:
            continue
        source = str(row["source"])
        if latest is None or day > latest[1]:
            latest = (name, day, source)
    if latest is None:
        raise ArenaError("CUTOFFS has no dated training cutoffs")
    return latest


def latest_cutoff() -> date:
    return latest_cutoff_entry()[1]


def first_allowed_issue() -> datetime:
    bound = latest_cutoff() + timedelta(days=1)
    return datetime(bound.year, bound.month, bound.day, tzinfo=timezone.utc)


def cutoff_records() -> dict[str, dict[str, str | None]]:
    return {
        name: {
            "cutoff": row["cutoff"],
            "source": row["source"],
            "note": row["note"],
        }
        for name, row in CUTOFFS.items()
    }


def yesterday_issue(now: datetime) -> datetime:
    day = ensure_utc(now).date() - timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def refuse_before_cutoffs(start: datetime) -> None:
    start = as_utc_hour(start)
    allowed = first_allowed_issue()
    name, bound, source = latest_cutoff_entry()
    if start < allowed:
        raise BackfillCutoff(
            f"refusing to backfill before the latest training cutoff "
            f"{bound.isoformat()} ({name}, {source}); "
            f"first allowed issue_time is {format_utc(allowed)}"
        )


def issue_schedule(
    start: datetime,
    end: datetime,
    *,
    step_hours: int = STEP_HOURS,
) -> list[datetime]:
    start = as_utc_hour(start)
    end = as_utc_hour(end)
    if step_hours < 1:
        raise ArenaError("step_hours must be at least 1")
    refuse_before_cutoffs(start)
    stamps: list[datetime] = []
    stamp = start
    delta = timedelta(hours=step_hours)
    while stamp <= end:
        stamps.append(stamp)
        stamp = stamp + delta
    return stamps


def retrospective_forecast_files(root: Path) -> list[Path]:
    directory = root / "data" / "retrospective" / "forecasts"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*/*.jsonl") if path.is_file())


def load_retrospective_file(root: Path, path: Path) -> list[Forecast]:
    rel = path.relative_to(root).as_posix()
    match = RETROSPECTIVE_FORECAST_REL.fullmatch(rel)
    if match is None:
        raise ForecastFormat(f"unexpected retrospective path: {rel}")
    text = path.read_text(encoding="utf-8")
    if text == "" or not text.endswith("\n"):
        raise ForecastFormat(f"{rel} must be non-empty JSONL ending in a newline")
    rows: list[Forecast] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise ForecastFormat(f"{rel}:{lineno} is blank")
        try:
            row = load_forecast_line(line)
        except ForecastFormat as exc:
            raise ForecastFormat(f"{rel}:{lineno}: {exc}") from exc
        if retrospective_forecast_rel(row.issue_time) != rel:
            raise ForecastFormat(f"{rel}:{lineno} issue_time does not match the path")
        rows.append(row)
    return rows


def load_retrospective_forecasts(root: Path) -> list[Forecast]:
    rows: list[Forecast] = []
    for path in retrospective_forecast_files(root):
        rows.extend(load_retrospective_file(root, path))
    return rows


def _write_retrospective_hour(root: Path, forecasts: list[Forecast]) -> Path:
    if not forecasts:
        raise ArenaError("refusing to seal an empty retrospective file")
    rel = retrospective_forecast_rel(forecasts[0].issue_time)
    if not rel.startswith("data/retrospective/"):
        raise ArenaError(f"refusing to write live path {rel}")
    payload = encode_forecasts(forecasts)
    path = root / rel
    if path.exists():
        if path.read_bytes() == payload:
            return path
        raise ForecastImmutable(f"{rel} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return path


def _forecasts_for_issue(
    root: Path,
    issue_time: datetime,
    products: list[str],
    models: list[Forecaster],
    sha: str,
) -> list[Forecast]:
    batch: list[Forecast] = []
    for product in products:
        history = log_price_history(root, product, issue_time)
        for model in models:
            quoted = model.forecast(history, list(HORIZON_HOURS), list(QUANTILES))
            version = getattr(model, "version", "1")
            for horizon in HORIZON_HOURS:
                batch.append(
                    Forecast(
                        issue_time=issue_time,
                        target_time=issue_time + timedelta(hours=horizon),
                        model=model.name,
                        product=product,
                        horizon=horizon,
                        quantiles=prices_from_log_quantiles(quoted[horizon]),
                        model_version=str(version),
                        code_git_sha=sha,
                    )
                )
    return batch


def score_retrospective(root: Path, *, now: datetime | None = None) -> dict:
    moment = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    forecasts = load_retrospective_forecasts(root)
    actuals = {(row.symbol, row.time): row.close for row in load_observations(root)}
    board = build_leaderboard(forecasts, actuals, moment)
    board["cutoffs"] = cutoff_records()
    board["latest_cutoff"] = latest_cutoff().isoformat()
    board["step_hours"] = STEP_HOURS
    return label_retrospective(board)


def write_retrospective_scores(root: Path, board: dict) -> tuple[Path, Path]:
    labeled = label_retrospective(board)
    labeled["cutoffs"] = cutoff_records()
    labeled["latest_cutoff"] = latest_cutoff().isoformat()
    retro = retrospective_leaderboard_path(root)
    retro.parent.mkdir(parents=True, exist_ok=True)
    temporary = retro.with_suffix(retro.suffix + ".tmp")
    temporary.write_text(json.dumps(labeled, indent=2) + "\n", encoding="utf-8")
    temporary.replace(retro)
    live = attach_retrospective(root, labeled)
    return retro, live


def run_backfill(
    root: Path,
    *,
    products: list[str] | None = None,
    model_names: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime | None = None,
    step_hours: int = STEP_HOURS,
) -> BackfillResult:
    clock = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    origin = as_utc_hour(start) if start is not None else DEFAULT_ORIGIN
    stop = as_utc_hour(end) if end is not None else yesterday_issue(clock)
    if step_hours != STEP_HOURS:
        raise ArenaError(f"retrospective backfill issues every {STEP_HOURS} hours")
    refuse_before_cutoffs(origin)
    chosen_products = list(DEFAULT_PRODUCTS if products is None else products)
    if not chosen_products:
        raise ArenaError("need at least one product")
    missing = [name for name in (model_names or []) if name not in CUTOFFS]
    if missing:
        raise ArenaError(f"no published training cutoff recorded for {missing[0]}")
    models = load_forecasters(model_names)
    unlabeled = [model.name for model in models if model.name not in CUTOFFS]
    if unlabeled:
        raise ArenaError(f"no published training cutoff recorded for {unlabeled[0]}")
    sha = code_git_sha(root)
    issued: list[datetime] = []
    skipped: list[tuple[datetime, str]] = []
    n_forecasts = 0
    live_manifest = manifest_path(root)
    before_hash = live_manifest.read_bytes() if live_manifest.is_file() else b""
    for issue_time in issue_schedule(origin, stop, step_hours=step_hours):
        try:
            batch = _forecasts_for_issue(root, issue_time, chosen_products, models, sha)
            _write_retrospective_hour(root, batch)
        except InsufficientHistory as exc:
            skipped.append((issue_time, str(exc)))
            continue
        issued.append(issue_time)
        n_forecasts += len(batch)
    after_hash = live_manifest.read_bytes() if live_manifest.is_file() else b""
    if after_hash != before_hash:
        raise ArenaError("retrospective backfill must not touch the live manifest")
    board = score_retrospective(root, now=clock)
    board["window"] = {
        "start": format_utc(origin),
        "end": format_utc(stop),
        "step_hours": step_hours,
    }
    retro_path, live_path = write_retrospective_scores(root, board)
    return BackfillResult(
        start=origin,
        end=stop,
        issued=issued,
        skipped=skipped,
        n_forecasts=n_forecasts,
        retrospective_path=retro_path,
        leaderboard_path=live_path,
        board=board,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from arena.cli import _extract_root, _symbols
    from arena.paths import find_root
    from arena.timeutil import parse_utc

    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        root_override, raw = _extract_root(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(prog="backfill")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--product", action="append", default=None)
    parser.add_argument("--model", action="append", default=None)
    parser.add_argument("--start", default=None, help="first UTC issue hour, default 2025-11-01T00:00:00Z")
    parser.add_argument("--end", default=None, help="last UTC issue hour, default yesterday 00:00 UTC")
    args = parser.parse_args(raw)
    root = root_override or args.root
    root = root.resolve() if root is not None else find_root()
    try:
        result = run_backfill(
            root,
            products=_symbols(args.product),
            model_names=args.model,
            start=parse_utc(args.start) if args.start else None,
            end=parse_utc(args.end) if args.end else None,
        )
    except (ArenaError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"RETROSPECTIVE {format_utc(result.start)} .. {format_utc(result.end)} "
        f"step={STEP_HOURS}h latest_cutoff={latest_cutoff().isoformat()}"
    )
    print(f"issued {len(result.issued)} hours ({result.n_forecasts} forecasts)")
    print(f"skipped {len(result.skipped)}")
    print(f"wrote {result.retrospective_path.relative_to(root).as_posix()}")
    print(f"updated {leaderboard_path(root).relative_to(root).as_posix()} retrospective")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
