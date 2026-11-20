from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from arena.models.naive import NaiveLast
from arena.models.random_walk import RandomWalk
from arena.schema import QUANTILE_KEYS
from arena.series import history_through

ISSUED = datetime(2026, 1, 8, tzinfo=timezone.utc)


def _series(values: list[float], end: datetime = ISSUED) -> pd.Series:
    index = pd.date_range(end - timedelta(hours=len(values) - 1), periods=len(values), freq="h", tz="UTC")
    return pd.Series(values, index=index, dtype="float64")


def _spread(quantiles: dict[str, float]) -> float:
    return quantiles["0.9"] - quantiles["0.1"]


def test_history_keeps_only_the_consecutive_suffix() -> None:
    index = pd.DatetimeIndex(
        [
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 4, tzinfo=timezone.utc),
        ]
    )
    series = pd.Series([1.0, 2.0, 3.0], index=index)
    with pytest.raises(Exception, match="consecutive"):
        history_through(series, index[-1].to_pydatetime(), 2)
    kept = history_through(series, index[-1].to_pydatetime(), 1)
    assert list(kept.values) == [3.0]


def test_naive_quotes_the_last_close() -> None:
    forecasts = NaiveLast().forecast("BTC-USD", _series([80.0, 90.0, 100.0]), ISSUED)
    assert [row.horizon for row in forecasts] == [1, 4, 24]
    assert all(row.quantiles[key] == 100.0 for row in forecasts for key in QUANTILE_KEYS)


def test_random_walk_quantiles_widen_with_horizon() -> None:
    rng = np.random.default_rng(0)
    returns = rng.normal(0.0, 0.01, size=80)
    prices = (100.0 * np.exp(np.cumsum(returns))).tolist()
    forecasts = RandomWalk().forecast("BTC-USD", _series(prices), ISSUED)
    by_horizon = {row.horizon: row.quantiles for row in forecasts}
    for quantiles in by_horizon.values():
        levels = [quantiles[key] for key in QUANTILE_KEYS]
        assert levels == sorted(levels)
        assert all(level > 0 for level in levels)
    assert _spread(by_horizon[24]) > _spread(by_horizon[1])


def _noisy_prices() -> pd.Series:
    rng = np.random.default_rng(1)
    returns = rng.normal(0.0, 0.008, size=160)
    return _series((100.0 * np.exp(np.cumsum(returns))).tolist())


def test_garch_is_deterministic() -> None:
    pytest.importorskip("arch")
    from arena.models.garch import GarchVol
    from arena.schema import dump_forecast

    prices = _noisy_prices()
    model = GarchVol()
    first = model.forecast("BTC-USD", prices, ISSUED)
    second = model.forecast("BTC-USD", prices, ISSUED)
    assert [dump_forecast(row) for row in first] == [dump_forecast(row) for row in second]
    for row in first:
        assert list(row.quantiles) == list(QUANTILE_KEYS)


def test_torch_quantile_is_deterministic() -> None:
    pytest.importorskip("torch")
    from arena.models.torch_quantile import TorchQuantile
    from arena.schema import dump_forecast

    prices = _noisy_prices()
    model = TorchQuantile()
    first = model.forecast("BTC-USD", prices, ISSUED)
    second = model.forecast("BTC-USD", prices, ISSUED)
    assert [dump_forecast(row) for row in first] == [dump_forecast(row) for row in second]
    for row in first:
        assert list(row.quantiles) == list(QUANTILE_KEYS)
