from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from arena.models.foundation import (
    CONTEXT_HOURS,
    interpolate_quantiles,
    prepare_forecast,
    quote_from_horizon_quantiles,
)


def test_interpolate_copies_native_levels_and_midpoints() -> None:
    native = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
    values = np.stack([native, native + 1.0], axis=0)
    mapped = interpolate_quantiles(native, values, [0.1, 0.25, 0.5, 0.75, 0.9])
    np.testing.assert_allclose(mapped[0], [0.1, 0.25, 0.5, 0.75, 0.9])
    np.testing.assert_allclose(mapped[1], [1.1, 1.25, 1.5, 1.75, 1.9])


def test_interpolate_rejects_extrapolation() -> None:
    native = np.array([0.1, 0.5, 0.9])
    values = np.zeros((1, 3))
    with pytest.raises(ValueError, match="outside native grid"):
        interpolate_quantiles(native, values, [0.05, 0.5])


def test_quote_reads_steps_1_4_24() -> None:
    mapped = np.arange(24 * 5, dtype=np.float64).reshape(24, 5)
    quoted = quote_from_horizon_quantiles(mapped, [1, 4, 24], [0.1, 0.25, 0.5, 0.75, 0.9])
    assert quoted[1][0.1] == 0.0
    assert quoted[4][0.1] == float(3 * 5)
    assert quoted[24][0.9] == float(23 * 5 + 4)


def test_context_window_is_the_last_512_closed_points() -> None:
    n = CONTEXT_HOURS + 40
    values = np.arange(n, dtype=np.float64)
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    history = pd.Series(values, index=index)
    context, hours, levels = prepare_forecast(history, [1, 4, 24], [0.1, 0.5, 0.9])
    np.testing.assert_array_equal(context, values[-CONTEXT_HOURS:])
    assert hours == [1, 4, 24]
    assert levels == [0.1, 0.5, 0.9]
    # Values after the last index of a prefix view cannot leak in.
    closed = history.iloc[:CONTEXT_HOURS]
    from_closed, _, _ = prepare_forecast(closed, [1], [0.5])
    from_prefix, _, _ = prepare_forecast(history.iloc[:CONTEXT_HOURS], [1], [0.5])
    np.testing.assert_array_equal(from_closed, from_prefix)
