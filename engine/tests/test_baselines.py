from __future__ import annotations

import numpy as np
import pandas as pd

from arena.models.base import Forecaster
from arena.models.baselines import AR1, EWMADrift, NoChange, _ewma_drift

HORIZONS = [1, 4, 24]
QUANTILES = [0.1, 0.25, 0.5, 0.75, 0.9]


def _log_history(n: int = 800, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    log_prices = np.cumsum(rng.normal(0.0002, 0.01, size=n))
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.Series(log_prices, index=index, dtype="float64")


def _with_future(history: pd.Series, extra: int = 48, seed: int = 99) -> pd.Series:
    rng = np.random.default_rng(seed)
    future = history.iloc[-1] + np.cumsum(rng.normal(0.05, 0.02, size=extra))
    index = pd.date_range(history.index[-1] + pd.Timedelta(hours=1), periods=extra, freq="h", tz="UTC")
    return pd.concat([history, pd.Series(future, index=index, dtype="float64")])


def _models() -> list[Forecaster]:
    return [NoChange(), EWMADrift(), AR1()]


def test_nochange_median_is_the_last_log_price() -> None:
    history = _log_history()
    quoted = NoChange().forecast(history, HORIZONS, QUANTILES)
    last = float(history.iloc[-1])
    for horizon in HORIZONS:
        assert quoted[horizon][0.5] == last
        levels = [quoted[horizon][q] for q in QUANTILES]
        assert levels == sorted(levels)


def test_ewma_median_is_last_plus_drift_times_horizon() -> None:
    history = _log_history()
    quoted = EWMADrift().forecast(history, HORIZONS, QUANTILES)
    last = float(history.iloc[-1])
    drift = _ewma_drift(history.to_numpy(dtype=np.float64)[-720:])
    for horizon in HORIZONS:
        assert quoted[horizon][0.5] == last + drift * horizon
        levels = [quoted[horizon][q] for q in QUANTILES]
        assert levels == sorted(levels)


def test_ar1_quantiles_are_log_prices_and_widen_with_horizon() -> None:
    history = _log_history()
    quoted = AR1().forecast(history, HORIZONS, QUANTILES)
    last = float(history.iloc[-1])
    for horizon in HORIZONS:
        levels = [quoted[horizon][q] for q in QUANTILES]
        assert levels == sorted(levels)
        assert all(np.isfinite(levels))
        # Residual noise compounds, so the path stays near the last log price.
        assert abs(quoted[horizon][0.5] - last) < 1.0
    assert quoted[24][0.9] - quoted[24][0.1] > quoted[1][0.9] - quoted[1][0.1]


def test_forecasts_ignore_values_after_the_last_index() -> None:
    history = _log_history()
    extended = _with_future(history)
    closed_view = extended.iloc[: len(history)]
    assert closed_view.index[-1] == history.index[-1]
    assert extended.iloc[len(history)] != history.iloc[-1]

    for model in _models():
        from_closed = model.forecast(history, HORIZONS, QUANTILES)
        from_view = model.forecast(closed_view, HORIZONS, QUANTILES)
        assert from_closed == from_view

        leaked = model.forecast(extended, HORIZONS, QUANTILES)
        assert leaked != from_closed
