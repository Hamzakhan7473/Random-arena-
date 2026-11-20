from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from arena.errors import ForecastFormat
from arena.timeutil import as_utc_hour, format_utc

QUANTILES: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9)
QUANTILE_KEYS: tuple[str, ...] = ("0.1", "0.25", "0.5", "0.75", "0.9")
HORIZON_HOURS: tuple[int, ...] = (1, 4, 24)
HORIZONS: tuple[str, ...] = ("1h", "4h", "24h")
HORIZON_LABEL: dict[int, str] = {1: "1h", 4: "4h", 24: "24h"}
HORIZON_DELTA: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}
Horizon = str

if tuple(str(level) for level in QUANTILES) != QUANTILE_KEYS:
    raise RuntimeError("quantile key contract drifted from str(level)")
if tuple(HORIZON_LABEL[hour] for hour in HORIZON_HOURS) != HORIZONS:
    raise RuntimeError("horizon labels drifted from horizon hours")


def parse_horizon(value: Any) -> int:
    if isinstance(value, str) and value.endswith("h") and value[:-1].isdigit():
        value = int(value[:-1])
    hours = int(value)
    if hours not in HORIZON_HOURS:
        raise ValueError(f"horizon must be one of {list(HORIZON_HOURS)}")
    return hours


class Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, protected_namespaces=())

    issue_time: datetime = Field(validation_alias=AliasChoices("issue_time", "issued_at"))
    target_time: datetime
    model: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        validation_alias=AliasChoices("model", "model_id"),
    )
    product: str = Field(
        min_length=1,
        pattern=r"^[A-Z0-9][A-Z0-9._-]*$",
        validation_alias=AliasChoices("product", "symbol"),
    )
    horizon: int
    quantiles: dict[str, float]
    model_version: str = Field(default="1", min_length=1)
    code_git_sha: str = Field(default="unknown", min_length=1)

    @field_validator("issue_time", "target_time")
    @classmethod
    def _exact_hour(cls, value: datetime) -> datetime:
        try:
            return as_utc_hour(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("horizon", mode="before")
    @classmethod
    def _horizon(cls, value: object) -> int:
        try:
            return parse_horizon(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _contract(self) -> Forecast:
        if set(self.quantiles) != set(QUANTILE_KEYS):
            raise ValueError(f"quantiles must be exactly {list(QUANTILE_KEYS)}")
        ordered = {key: float(self.quantiles[key]) for key in QUANTILE_KEYS}
        values = [ordered[key] for key in QUANTILE_KEYS]
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("quantile prices must be finite and positive")
        if any(values[i] > values[i + 1] for i in range(len(values) - 1)):
            raise ValueError("quantile prices must be non-decreasing")
        self.quantiles = ordered
        if self.target_time != self.issue_time + timedelta(hours=self.horizon):
            raise ValueError("target_time must equal issue_time plus the horizon")
        return self

    @property
    def model_id(self) -> str:
        return self.model

    @property
    def symbol(self) -> str:
        return self.product

    @property
    def issued_at(self) -> datetime:
        return self.issue_time


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, pattern=r"^[A-Z0-9][A-Z0-9._-]*$")
    time: datetime
    close: float = Field(gt=0)

    @field_validator("time")
    @classmethod
    def _exact_hour(cls, value: datetime) -> datetime:
        try:
            return as_utc_hour(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("close")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("close must be finite")
        return value


def dump_forecast(forecast: Forecast) -> str:
    payload = {
        "issue_time": format_utc(forecast.issue_time),
        "target_time": format_utc(forecast.target_time),
        "model": forecast.model,
        "product": forecast.product,
        "horizon": forecast.horizon,
        "quantiles": {key: forecast.quantiles[key] for key in QUANTILE_KEYS},
        "model_version": forecast.model_version,
        "code_git_sha": forecast.code_git_sha,
    }
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


def dump_observation(observation: Observation) -> str:
    payload = {
        "symbol": observation.symbol,
        "time": format_utc(observation.time),
        "close": observation.close,
    }
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


def load_forecast_line(line: str) -> Forecast:
    try:
        return Forecast.model_validate_json(line)
    except Exception as exc:
        raise ForecastFormat(str(exc)) from exc
