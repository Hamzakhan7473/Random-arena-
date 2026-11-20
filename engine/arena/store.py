from __future__ import annotations

from datetime import datetime
from pathlib import Path

from arena.errors import ForecastFormat
from arena.paths import FORECAST_REL, forecast_rel
from arena.schema import Forecast, load_forecast_line


def forecast_files(root: Path) -> list[Path]:
    directory = root / "data" / "forecasts"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*/*.jsonl") if path.is_file())


def load_forecast_file(root: Path, path: Path) -> list[Forecast]:
    rel = path.relative_to(root).as_posix()
    match = FORECAST_REL.fullmatch(rel)
    if match is None:
        raise ForecastFormat(f"unexpected forecast path: {rel}")
    day, hour = match.group(1), match.group(2)
    if not 0 <= int(hour) <= 23:
        raise ForecastFormat(f"forecast hour is out of range: {rel}")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise ForecastFormat(f"forecast path is not a calendar day: {rel}") from exc
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
        if forecast_rel(row.issue_time) != rel:
            raise ForecastFormat(f"{rel}:{lineno} issue_time does not match the path")
        rows.append(row)
    return rows


def load_all_forecasts(root: Path) -> list[Forecast]:
    rows: list[Forecast] = []
    for path in forecast_files(root):
        rows.extend(load_forecast_file(root, path))
    return rows
