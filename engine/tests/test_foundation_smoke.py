"""Smoke each foundation adapter on synthetic log prices.

Skipped when the extra or the Hugging Face weights are missing so default CI
stays offline. With ``pip install -e "./engine[foundation]"`` and a Hub cache
(or network) the test loads each model once, forecasts horizons 1/4/24, and
checks shape plus quantile monotonicity.
"""

from __future__ import annotations

import importlib

import numpy as np
import pandas as pd
import pytest

HORIZONS = [1, 4, 24]
QUANTILES = [0.1, 0.25, 0.5, 0.75, 0.9]

ADAPTERS = [
    pytest.param("chronos", "arena.models.chronos_bolt", "ChronosBoltSmall", id="chronos-bolt-small"),
    pytest.param("chronos", "arena.models.chronos2", "Chronos2", id="chronos-2"),
    pytest.param("timesfm", "arena.models.timesfm", "TimesFM25", id="timesfm-2.5"),
]


def _synthetic_log_prices(n: int = 512, seed: int = 7) -> pd.Series:
    rng = np.random.default_rng(seed)
    log_prices = np.cumsum(rng.normal(0.0001, 0.008, size=n))
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.Series(log_prices, index=index, dtype="float64")


def _unavailable(exc: BaseException) -> bool:
    if isinstance(exc, (OSError, FileNotFoundError, MemoryError, TimeoutError)):
        return True
    text = str(exc).lower()
    needles = (
        "huggingface",
        "401 client error",
        "403 client error",
        "connection",
        "offline",
        "failed to download",
        "local_files_only",
    )
    return any(needle in text for needle in needles)


@pytest.mark.parametrize("extra, module_name, class_name", ADAPTERS)
def test_adapter_shape_and_monotonic_quantiles(extra: str, module_name: str, class_name: str) -> None:
    pytest.importorskip(extra)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    history = _synthetic_log_prices()
    try:
        quoted = cls().forecast(history, HORIZONS, QUANTILES)
    except Exception as exc:
        if _unavailable(exc):
            pytest.skip(f"{class_name} weights or runtime unavailable: {exc}")
        raise

    assert set(quoted) == set(HORIZONS)
    for horizon in HORIZONS:
        assert set(quoted[horizon]) == set(QUANTILES)
        values = [quoted[horizon][q] for q in QUANTILES]
        assert all(np.isfinite(values))
        assert values == sorted(values) or np.all(np.diff(values) >= -1e-9)
        assert len(values) == len(QUANTILES)
