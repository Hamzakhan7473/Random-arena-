from __future__ import annotations

from datetime import datetime

from arena.schema import HORIZON_DELTA, HORIZONS, QUANTILE_KEYS, Forecast, Horizon


def build_forecast(
    model_id: str,
    symbol: str,
    issued_at: datetime,
    horizon: Horizon,
    quantiles: dict[str, float],
) -> Forecast:
    ordered = {key: float(quantiles[key]) for key in QUANTILE_KEYS}
    return Forecast(
        model_id=model_id,
        symbol=symbol,
        issued_at=issued_at,
        horizon=horizon,
        target_time=issued_at + HORIZON_DELTA[horizon],
        quantiles=ordered,
    )


def forecasts_for_horizons(
    model_id: str,
    symbol: str,
    issued_at: datetime,
    by_horizon: dict[str, dict[str, float]],
) -> list[Forecast]:
    return [
        build_forecast(model_id, symbol, issued_at, horizon, by_horizon[horizon])
        for horizon in HORIZONS
    ]
