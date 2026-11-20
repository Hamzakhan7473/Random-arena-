from __future__ import annotations

from datetime import datetime
from pathlib import Path

from arena.publish import PublishResult
from arena.run_forecasts import run_forecasts


def run_forecast(
    root: Path,
    *,
    symbols: list[str],
    issued_at: datetime,
    now: datetime,
    model_names: list[str] | None = None,
) -> PublishResult:
    return run_forecasts(
        root,
        products=symbols,
        model_names=model_names,
        issue_time=issued_at,
        now=now,
    )
