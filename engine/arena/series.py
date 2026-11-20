from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from arena.actuals import load_observations
from arena.errors import ActualConflict, InsufficientHistory
from arena.timeutil import as_utc_hour


def price_series(root: Path, symbol: str, issued_at: datetime) -> pd.Series:
    issued_at = as_utc_hour(issued_at)
    observations = [
        row
        for row in load_observations(root)
        if row.symbol == symbol and row.time <= issued_at
    ]
    if not observations:
        return pd.Series(dtype="float64")
    index = pd.DatetimeIndex(pd.to_datetime([row.time for row in observations], utc=True))
    series = pd.Series([row.close for row in observations], index=index, dtype="float64")
    if series.index.has_duplicates:
        raise ActualConflict(f"duplicate actuals for {symbol}")
    return series.sort_index()


def history_through(prices: pd.Series, issued_at: datetime, min_points: int) -> pd.Series:
    """Return the consecutive hourly suffix ending at issued_at."""
    issued_at = as_utc_hour(issued_at)
    if prices.empty:
        raise InsufficientHistory("no prices")
    series = prices.sort_index()
    if series.index.has_duplicates:
        raise InsufficientHistory("duplicate timestamps")
    target = pd.Timestamp(issued_at)
    if target not in series.index:
        raise InsufficientHistory(f"no close at {issued_at.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    series = series.loc[:target]
    index = series.index
    start = 0
    for position in range(1, len(index)):
        if index[position] - index[position - 1] != timedelta(hours=1):
            start = position
    series = series.iloc[start:]
    if len(series) < min_points:
        raise InsufficientHistory(
            f"need {min_points} consecutive hourly closes ending at issued_at, found {len(series)}"
        )
    return series
