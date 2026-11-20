from __future__ import annotations

import numpy as np
import pandas as pd

from arena.errors import InsufficientHistory
from arena.models.base import check_horizons_and_quantiles, closed_log_prices

# Arena job: 512 closed hourly log prices in, one 24-step forecast out.
CONTEXT_HOURS = 512
PREDICTION_LENGTH = 24


def context_window(history: pd.Series) -> np.ndarray:
    """Last 512 finite log prices, oldest first. Later bars cannot leak in."""
    log_prices = closed_log_prices(history)
    if log_prices.size < 1:
        raise InsufficientHistory("need at least one closed log price")
    if log_prices.size > CONTEXT_HOURS:
        return log_prices[-CONTEXT_HOURS:]
    return log_prices


def interpolate_quantiles(
    native_levels: np.ndarray,
    native_values: np.ndarray,
    requested_levels: list[float],
) -> np.ndarray:
    """Map a model's native quantile grid onto requested levels.

    ``native_values`` has shape ``(horizon, n_native)``. Interpolation is linear
    in quantile-level space along the last axis (the PPF at q=0.25 is halfway
    between the PPF at 0.2 and 0.3 when those are the neighboring native
    levels). Exact native levels are copied. Requested levels must lie inside
    the native range; these models are not asked to extrapolate tails.
    """
    levels = np.asarray(native_levels, dtype=np.float64)
    values = np.asarray(native_values, dtype=np.float64)
    if levels.ndim != 1 or levels.size < 2:
        raise ValueError("native quantile grid must be a 1-d vector of length >= 2")
    if values.ndim != 2 or values.shape[1] != levels.size:
        raise ValueError("native_values must have shape (horizon, n_native_quantiles)")
    if np.any(np.diff(levels) <= 0):
        raise ValueError("native quantile levels must be strictly increasing")
    requested = np.asarray(requested_levels, dtype=np.float64)
    if requested.min() < levels[0] - 1e-12 or requested.max() > levels[-1] + 1e-12:
        raise ValueError(
            f"requested quantiles {requested.tolist()} fall outside native grid "
            f"{levels.tolist()}; adapters do not extrapolate"
        )
    mapped = np.empty((values.shape[0], requested.size), dtype=np.float64)
    for step in range(values.shape[0]):
        mapped[step] = np.interp(requested, levels, values[step])
    return mapped


def quote_from_horizon_quantiles(
    mapped: np.ndarray,
    horizons: list[int],
    quantiles: list[float],
) -> dict[int, dict[float, float]]:
    """Read horizons 1..H off a single (H, Q) array. Step h is row h-1."""
    if mapped.ndim != 2 or mapped.shape[0] < max(horizons):
        raise ValueError(
            f"expected at least {max(horizons)} predicted steps, got {mapped.shape}"
        )
    if mapped.shape[1] != len(quantiles):
        raise ValueError("mapped quantile axis does not match requested levels")
    quoted: dict[int, dict[float, float]] = {}
    for horizon in horizons:
        row = mapped[horizon - 1]
        quoted[horizon] = {level: float(row[i]) for i, level in enumerate(quantiles)}
    return quoted


def prepare_forecast(
    history: pd.Series,
    horizons: list[int],
    quantiles: list[float],
) -> tuple[np.ndarray, list[int], list[float]]:
    hours, levels = check_horizons_and_quantiles(horizons, quantiles)
    if max(hours) > PREDICTION_LENGTH:
        raise ValueError(f"horizons must be <= {PREDICTION_LENGTH}, got {hours}")
    return context_window(history), hours, levels
