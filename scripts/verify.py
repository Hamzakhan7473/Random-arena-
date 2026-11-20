#!/usr/bin/env python3
"""Check a sealed forecast file against the manifest and git commit time.

For each forecast JSONL:

1. SHA-256 of the file must match the ``<issue_time> <path> <sha256>`` line in
   ``data/manifest/hashes.log``.
2. ``git log`` on that path must show a committer time strictly earlier than
   every ``target_time`` in the file.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _repo_root(start: Path | None = None) -> Path:
    here = Path(__file__).resolve()
    seeds = []
    if start is not None:
        seeds.append(start.resolve())
    seeds.append(Path.cwd().resolve())
    seeds.append(here.parent.parent)
    seen: set[Path] = set()
    for seed in seeds:
        for candidate in (seed, *seed.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / "data" / "manifest").exists() or (candidate / "data" / "forecasts").exists():
                return candidate
            if (candidate / "ARCHITECTURE.md").is_file() and (candidate / "data").is_dir():
                return candidate
    raise SystemExit("error: repo root not found")


def _ensure_engine_on_path() -> None:
    engine = str(Path(__file__).resolve().parent.parent / "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-pager", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _forecast_files(root: Path, requested: list[str]) -> list[Path]:
    if requested:
        files = []
        for item in requested:
            path = Path(item)
            if not path.is_absolute():
                path = (root / path).resolve()
            files.append(path)
        return files
    directory = root / "data" / "forecasts"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*/*.jsonl") if path.is_file())


def verify_file(root: Path, path: Path) -> list[str]:
    from arena.manifest import lookup_digest, sha256_file
    from arena.store import load_forecast_file
    from arena.timeutil import parse_utc

    errors: list[str] = []
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return [f"{path} is not inside the repository"]
    if not path.is_file():
        return [f"{rel} is missing"]

    digest = sha256_file(path)
    recorded = lookup_digest(root, rel)
    if recorded is None:
        errors.append(f"{rel} is not in data/manifest/hashes.log")
    elif recorded != digest:
        errors.append(f"{rel} hash {digest} does not match manifest {recorded}")
    else:
        print(f"{rel}")
        print(f"  manifest sha256: {digest} OK")

    try:
        rows = load_forecast_file(root, path)
    except Exception as exc:
        errors.append(f"{rel} is not valid forecast JSONL: {exc}")
        rows = []

    log = _git(root, "log", "-1", "--format=%cI%n%H%n%s", "--", rel)
    if log.returncode != 0 or not log.stdout.strip():
        errors.append(f"{rel}: git log found no commit")
        return errors
    committed_raw, commit, subject = (log.stdout.splitlines() + ["", "", ""])[:3]
    try:
        committed_at = parse_utc(committed_raw)
    except ValueError:
        errors.append(f"{rel}: git log committer time is not parseable: {committed_raw}")
        return errors
    print(f"  git log {commit[:12]} {committed_raw} ({subject})")

    targets = sorted({row.target_time for row in rows})
    if not targets:
        return errors
    earliest = targets[0]
    print(f"  earliest target_time: {earliest.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    late = [row for row in rows if committed_at >= row.target_time]
    if late:
        sample = late[0]
        errors.append(
            f"{rel} committer time {committed_at.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"is not before {sample.model} {sample.horizon}h target "
            f"{sample.target_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
    else:
        print("  commit is earlier than every target_time")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="repository root")
    parser.add_argument(
        "paths",
        nargs="*",
        help="forecast JSONL paths; default is every file under data/forecasts",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root is not None else _repo_root()
    _ensure_engine_on_path()
    files = _forecast_files(root, args.paths)
    if not files:
        print("no forecast files")
        return 0
    errors: list[str] = []
    for path in files:
        errors.extend(verify_file(root, path))
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
