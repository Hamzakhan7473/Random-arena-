from __future__ import annotations

import numpy as np
import pandas as pd

from arena.errors import InsufficientHistory
from arena.models.base import (
    Forecaster,
    check_horizons_and_quantiles,
    closed_log_prices,
    lookback_window,
)

EWMA_HALFLIFE_HOURS = 72
AR1_BOOTSTRAP_PATHS = 2000
AR1_SEED = 0


class NoChange(Forecaster):
    """Median is the last log price. Spread is empirical h-step log returns."""

    name = "no-change"
    version = "1"

    def forecast(
        self,
        history: pd.Series,
        horizons: list[int],
        quantiles: list[float],
    ) -> dict[int, dict[float, float]]:
        hours, levels = check_horizons_and_quantiles(horizons, quantiles)
        log_prices = lookback_window(closed_log_prices(history))
        last = float(log_prices[-1])
        return {
            horizon: _empirical_quantiles(last, _h_step_returns(log_prices, horizon), levels)
            for horizon in hours
        }


class EWMADrift(Forecaster):
    """NoChange spread around an EWMA 1-step drift scaled by the horizon."""

    name = "ewma-drift"
    version = "1"

    def forecast(
        self,
        history: pd.Series,
        horizons: list[int],
        quantiles: list[float],
    ) -> dict[int, dict[float, float]]:
        hours, levels = check_horizons_and_quantiles(horizons, quantiles)
        log_prices = lookback_window(closed_log_prices(history))
        last = float(log_prices[-1])
        drift = _ewma_drift(log_prices)
        return {
            horizon: _empirical_quantiles(
                last + drift * horizon,
                _h_step_returns(log_prices, horizon),
                levels,
            )
            for horizon in hours
        }


class AR1(Forecaster):
    """AR(1) on 1-step returns, iterated h steps; quantiles from residual bootstrap."""

    name = "ar1"
    version = "1"

    def forecast(
        self,
        history: pd.Series,
        horizons: list[int],
        quantiles: list[float],
    ) -> dict[int, dict[float, float]]:
        hours, levels = check_horizons_and_quantiles(horizons, quantiles)
        log_prices = lookback_window(closed_log_prices(history))
        intercept, phi, last_return, residuals = _fit_ar1(log_prices)
        last = float(log_prices[-1])
        rng = np.random.default_rng(AR1_SEED)
        return {
            horizon: _bootstrap_quantiles(
                last,
                last_return,
                intercept,
                phi,
                residuals,
                horizon,
                levels,
                rng,
            )
            for horizon in hours
        }


def _h_step_returns(log_prices: np.ndarray, horizon: int) -> np.ndarray:
    if horizon < 1:
        raise ValueError("horizon must be a positive integer")
    if len(log_prices) <= horizon:
        raise InsufficientHistory(
            f"need more than {horizon} closed hours for {horizon}-step returns, found {len(log_prices)}"
        )
    returns = log_prices[horizon:] - log_prices[:-horizon]
    if not np.isfinite(returns).all():
        raise InsufficientHistory("h-step log returns must be finite")
    return returns


def _empirical_quantiles(
    location: float,
    returns: np.ndarray,
    quantiles: list[float],
) -> dict[float, float]:
    # Center on the empirical median so the quoted median is `location`.
    offset = np.quantile(returns, quantiles) - np.quantile(returns, 0.5)
    return {level: float(location + shift) for level, shift in zip(quantiles, offset, strict=True)}


def _ewma_drift(log_prices: np.ndarray) -> float:
    if len(log_prices) < 2:
        raise InsufficientHistory("EWMA drift needs at least one 1-step return")
    one_step = np.diff(log_prices)
    drift = pd.Series(one_step, dtype="float64").ewm(halflife=EWMA_HALFLIFE_HOURS, adjust=True).mean().iloc[-1]
    if not np.isfinite(drift):
        raise InsufficientHistory("EWMA drift was not finite")
    return float(drift)


def _fit_ar1(log_prices: np.ndarray) -> tuple[float, float, float, np.ndarray]:
    if len(log_prices) < 4:
        raise InsufficientHistory("AR(1) needs at least three 1-step returns")
    returns = np.diff(log_prices)
    lagged = returns[:-1]
    observed = returns[1:]
    design = np.column_stack([np.ones(len(lagged)), lagged])
    coef, *_ = np.linalg.lstsq(design, observed, rcond=None)
    intercept = float(coef[0])
    phi = float(coef[1])
    residuals = observed - (intercept + phi * lagged)
    if not np.isfinite(residuals).all() or residuals.size == 0:
        raise InsufficientHistory("AR(1) residuals were not finite")
    return intercept, phi, float(returns[-1]), residuals


def _bootstrap_quantiles(
    last_log_price: float,
    last_return: float,
    intercept: float,
    phi: float,
    residuals: np.ndarray,
    horizon: int,
    quantiles: list[float],
    rng: np.random.Generator,
) -> dict[float, float]:
    draws = rng.choice(residuals, size=(AR1_BOOTSTRAP_PATHS, horizon), replace=True)
    previous = np.full(AR1_BOOTSTRAP_PATHS, last_return, dtype=np.float64)
    total = np.zeros(AR1_BOOTSTRAP_PATHS, dtype=np.float64)
    for step in range(horizon):
        previous = intercept + phi * previous + draws[:, step]
        total += previous
    quoted = np.quantile(last_log_price + total, quantiles)
    return {level: float(value) for level, value in zip(quantiles, quoted, strict=True)}
