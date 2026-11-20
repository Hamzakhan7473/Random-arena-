from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

from arena.errors import ActualConflict
from arena.ingest import (
    COLUMNS,
    empty_candles,
    product_store_path,
    read_candles,
    write_candles,
)
from arena.schema import Observation
from arena.timeutil import as_utc_hour


def load_observations(root: Path) -> list[Observation]:
    directory = root / "data" / "actuals"
    if not directory.is_dir():
        return []
    rows: list[Observation] = []
    for path in sorted(directory.glob("*.parquet")):
        product_id = path.stem
        candles = read_candles(path)
        for row in candles.itertuples(index=False):
            known_at = as_utc_hour(_as_utc_datetime(row.timestamp_utc) + timedelta(hours=1))
            rows.append(Observation(symbol=product_id, time=known_at, close=float(row.close)))
    return rows


def append_observations(root: Path, observations: list[Observation]) -> int:
    """Append closes for tests and local fixtures. Observation.time is the candle end."""
    added = 0
    by_product: dict[str, list[Observation]] = {}
    for observation in observations:
        by_product.setdefault(observation.symbol, []).append(observation)
    for product_id, group in by_product.items():
        path = product_store_path(root, product_id)
        stored = read_candles(path)
        incoming = _observations_to_candles(group)
        combined, n_new = _merge(stored, incoming, product_id)
        if n_new:
            write_candles(path, combined)
            added += n_new
    return added


def _observations_to_candles(observations: list[Observation]) -> pd.DataFrame:
    if not observations:
        return empty_candles()
    records = []
    for observation in observations:
        start = as_utc_hour(observation.time) - timedelta(hours=1)
        close = float(observation.close)
        records.append(
            {
                "timestamp_utc": start,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 0.0,
            }
        )
    frame = pd.DataFrame(records, columns=list(COLUMNS))
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    return frame


def _merge(stored: pd.DataFrame, incoming: pd.DataFrame, product_id: str) -> tuple[pd.DataFrame, int]:
    if incoming.empty:
        return stored, 0
    incoming = incoming.sort_values("timestamp_utc").drop_duplicates(subset=["timestamp_utc"], keep="last")
    if stored.empty:
        return incoming.reset_index(drop=True), int(len(incoming))
    index = stored.set_index("timestamp_utc")
    fresh = []
    for row in incoming.itertuples(index=False):
        stamp = pd.Timestamp(row.timestamp_utc)
        if stamp in index.index:
            previous = float(index.loc[stamp, "close"])
            if abs(previous - float(row.close)) > 1e-8:
                raise ActualConflict(
                    f"{product_id} at {stamp.isoformat()} is already {previous}, refusing {row.close}"
                )
            continue
        fresh.append(row)
    if not fresh:
        return stored, 0
    added = pd.DataFrame(fresh, columns=COLUMNS)
    combined = pd.concat([stored, added], ignore_index=True).sort_values("timestamp_utc")
    return combined.reset_index(drop=True), int(len(added))


def _as_utc_datetime(value: object):
    from datetime import datetime, timezone

    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    moment = stamp.to_pydatetime()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment
