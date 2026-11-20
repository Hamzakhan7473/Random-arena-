from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from arena.errors import ArenaError, ForecastImmutable, PublishedTooLate
from arena.manifest import append_hash, sha256_bytes
from arena.paths import forecast_rel
from arena.schema import HORIZON_HOURS, dump_forecast, Forecast
from arena.timeutil import ensure_utc


@dataclass(frozen=True)
class PublishResult:
    path: Path
    rel: str
    digest: str
    issue_time: datetime
    created: bool


def encode_forecasts(forecasts: list[Forecast]) -> bytes:
    order = {horizon: position for position, horizon in enumerate(HORIZON_HOURS)}
    ordered = sorted(
        forecasts,
        key=lambda row: (row.model, row.product, order[row.horizon]),
    )
    keys = [(row.model, row.product, row.horizon) for row in ordered]
    if len(keys) != len(set(keys)):
        raise ArenaError("a forecast file cannot contain a duplicate model, product, and horizon")
    issued = {row.issue_time for row in ordered}
    if len(issued) != 1:
        raise ArenaError("a forecast file holds one issue hour")
    payload = "".join(dump_forecast(row) + "\n" for row in ordered)
    return payload.encode("utf-8")


def publish_forecasts(root: Path, forecasts: list[Forecast], *, now: datetime) -> PublishResult:
    if not forecasts:
        raise ArenaError("refusing to seal an empty forecast file")
    now = ensure_utc(now)
    for row in forecasts:
        if row.issue_time > now:
            raise PublishedTooLate("issue_time is later than the publisher clock")
        if now >= row.target_time:
            raise PublishedTooLate(
                f"publisher clock {now.isoformat()} is not before target {row.target_time.isoformat()}"
            )
    payload = encode_forecasts(forecasts)
    digest = sha256_bytes(payload)
    issue_time = forecasts[0].issue_time
    rel = forecast_rel(issue_time)
    path = root / rel
    if path.exists():
        raise ForecastImmutable(f"{rel} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    append_hash(root, rel, digest, issue_time=issue_time)
    return PublishResult(path=path, rel=rel, digest=digest, issue_time=issue_time, created=True)
