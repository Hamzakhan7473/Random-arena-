from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from arena.errors import InsufficientHistory

LOOKBACK_HOURS = 720


class Forecaster(ABC):
    """A model that quotes log-price quantiles as of the last closed candle."""

    name: str
    version: str = "1"

    @abstractmethod
    def forecast(
        self,
        history: pd.Series,
        horizons: list[int],
        quantiles: list[float],
    ) -> dict[int, dict[float, float]]:
        """Return log-price quantiles keyed by horizon (hours) then quantile level.

        `history` is log price, oldest first, and must end at the last closed candle.
        Implementations may use only that series; values after the last index are
        not closed and must not be read.
        """


def closed_log_prices(history: pd.Series) -> np.ndarray:
    """Copy log prices through the last index so later bars cannot leak in."""
    if not isinstance(history, pd.Series):
        raise TypeError("history must be a pandas Series of log prices")
    if history.empty:
        raise InsufficientHistory("history is empty")
    values = history.to_numpy(dtype=np.float64, copy=True)
    if values.ndim != 1:
        raise InsufficientHistory("history must be one-dimensional")
    if not np.isfinite(values).all():
        raise InsufficientHistory("log prices must be finite")
    return values


def lookback_window(log_prices: np.ndarray, lookback: int = LOOKBACK_HOURS) -> np.ndarray:
    if lookback < 2:
        raise InsufficientHistory("lookback must be at least 2")
    if len(log_prices) < 2:
        raise InsufficientHistory("need at least two closed log prices")
    if len(log_prices) <= lookback:
        return log_prices
    return log_prices[-lookback:]


def check_horizons_and_quantiles(
    horizons: list[int],
    quantiles: list[float],
) -> tuple[list[int], list[float]]:
    if not horizons:
        raise ValueError("horizons must be non-empty")
    if not quantiles:
        raise ValueError("quantiles must be non-empty")
    hours: list[int] = []
    for horizon in horizons:
        if int(horizon) != horizon or int(horizon) < 1:
            raise ValueError("horizons must be positive integers")
        hours.append(int(horizon))
    levels: list[float] = []
    for level in quantiles:
        value = float(level)
        if not 0.0 < value < 1.0:
            raise ValueError("quantiles must be in (0, 1)")
        levels.append(value)
    return hours, levels
