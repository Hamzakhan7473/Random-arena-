from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from arena.timeutil import format_utc

FORECAST_REL = re.compile(r"^data/forecasts/(\d{4}-\d{2}-\d{2})/(\d{2})\.jsonl$")
RETROSPECTIVE_FORECAST_REL = re.compile(
    r"^data/retrospective/forecasts/(\d{4}-\d{2}-\d{2})/(\d{2})\.jsonl$"
)


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get("ARENA_ROOT")
    if env:
        return Path(env).resolve()
    seeds = []
    if start is not None:
        seeds.append(start.resolve())
    seeds.append(Path.cwd().resolve())
    seeds.extend(Path(__file__).resolve().parents)
    seen: set[Path] = set()
    for seed in seeds:
        for candidate in (seed, *seed.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / "ARCHITECTURE.md").is_file() and (candidate / "data").is_dir():
                return candidate
    raise FileNotFoundError("repo root not found; set ARENA_ROOT or pass --root")


def forecast_rel(issued_at: datetime) -> str:
    stamp = format_utc(issued_at)
    day, hour = stamp[:10], stamp[11:13]
    return f"data/forecasts/{day}/{hour}.jsonl"


def retrospective_forecast_rel(issued_at: datetime) -> str:
    stamp = format_utc(issued_at)
    day, hour = stamp[:10], stamp[11:13]
    return f"data/retrospective/forecasts/{day}/{hour}.jsonl"


def manifest_path(root: Path) -> Path:
    return root / "data" / "manifest" / "hashes.log"


def leaderboard_path(root: Path) -> Path:
    return root / "data" / "scores" / "leaderboard.json"


def history_path(root: Path) -> Path:
    return root / "data" / "scores" / "history.json"


def retrospective_leaderboard_path(root: Path) -> Path:
    return root / "data" / "retrospective" / "leaderboard.json"
