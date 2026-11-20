from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from arena.schema import QUANTILE_KEYS, Forecast

ISSUED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _quantiles(**overrides: float) -> dict[str, float]:
    values = {key: 100.0 + index for index, key in enumerate(QUANTILE_KEYS)}
    values.update(overrides)
    return values


def test_accepts_monotonic_quantiles() -> None:
    forecast = Forecast(
        model_id="naive-last",
        symbol="BTC-USD",
        issued_at=ISSUED,
        horizon="1h",
        target_time=ISSUED + timedelta(hours=1),
        quantiles=_quantiles(),
    )
    assert list(forecast.quantiles) == list(QUANTILE_KEYS)


def test_rejects_crossed_quantiles() -> None:
    with pytest.raises(ValidationError):
        Forecast(
            model_id="naive-last",
            symbol="BTC-USD",
            issued_at=ISSUED,
            horizon="1h",
            target_time=ISSUED + timedelta(hours=1),
            quantiles=_quantiles(**{"0.9": 1.0}),
        )


def test_rejects_wrong_quantile_keys() -> None:
    values = _quantiles()
    values["0.10"] = values.pop("0.1")
    with pytest.raises(ValidationError):
        Forecast(
            model_id="naive-last",
            symbol="BTC-USD",
            issued_at=ISSUED,
            horizon="1h",
            target_time=ISSUED + timedelta(hours=1),
            quantiles=values,
        )


def test_rejects_target_that_does_not_match_horizon() -> None:
    with pytest.raises(ValidationError):
        Forecast(
            model_id="naive-last",
            symbol="BTC-USD",
            issued_at=ISSUED,
            horizon="1h",
            target_time=ISSUED + timedelta(hours=4),
            quantiles=_quantiles(),
        )


def test_rejects_a_timestamp_that_is_not_an_hour() -> None:
    with pytest.raises(ValidationError):
        Forecast(
            model_id="naive-last",
            symbol="BTC-USD",
            issued_at=ISSUED + timedelta(minutes=5),
            horizon="1h",
            target_time=ISSUED + timedelta(hours=1),
            quantiles=_quantiles(),
        )
