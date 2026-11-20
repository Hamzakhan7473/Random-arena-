from datetime import datetime, timedelta, timezone

from arena.schema import QUANTILE_KEYS, Forecast
from arena.score import attach_retrospective, build_leaderboard, pinball, relative_error, write_history, write_scores
from arena.paths import history_path, leaderboard_path
import numpy as np

ISSUED0 = datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _quantiles(price: float) -> dict[str, float]:
    return {key: price for key in QUANTILE_KEYS}


def _forecast(model: str, issued: datetime, horizon: int, quote: float) -> Forecast:
    return Forecast(
        model=model,
        product="BTC-USD",
        issue_time=issued,
        horizon=horizon,
        target_time=issued + timedelta(hours=horizon),
        quantiles=_quantiles(quote),
        model_version="1",
        code_git_sha="unknown",
    )


def _path(n: int = 8, horizon: int = 1) -> tuple[list[datetime], dict[tuple[str, datetime], float], list[float], list[float]]:
    times = [ISSUED0 + timedelta(hours=i) for i in range(n + horizon)]
    prices = [100.0 * (1.01 ** i) for i in range(n + horizon)]
    actuals = {("BTC-USD", stamp): price for stamp, price in zip(times, prices, strict=True)}
    issued = times[:n]
    last = prices[:n]
    realized = prices[horizon : horizon + n]
    return issued, actuals, last, realized


def test_pinball_sides() -> None:
    assert pinball(0.0, -2.0, 0.25) == 0.5
    assert pinball(0.0, 2.0, 0.25) == 1.5


def test_nochange_clone_relative_error_is_exactly_one() -> None:
    horizon = 1
    issued, actuals, last, _realized = _path()
    forecasts = [
        _forecast("clone", stamp, horizon, quote)
        for stamp, quote in zip(issued, last, strict=True)
    ]
    pending = _forecast("clone", issued[-1] + timedelta(hours=48), 24, last[-1])
    board = build_leaderboard(forecasts + [pending], actuals, NOW)
    cells = [cell for cell in board["cells"] if cell["model"] == "clone" and cell["horizon"] == horizon]
    assert len(cells) == 1
    assert cells[0]["n_scored"] == len(issued)
    assert cells[0]["relative_error"] == 1.0
    assert cells[0]["mase"] == 1.0
    assert cells[0]["beats_random_walk"] is False
    assert board["n_pending"] == 1
    assert board["n_scored"] == len(issued)
    assert board["live_since"] == ISSUED0.strftime("%Y-%m-%d")
    hour = board["rows"][0]["by_horizon"]["1h"]
    assert hour["relative_error"] == 1.0
    assert hour["dm_pvalue"] is not None
    assert hour["relative_error_ci"] is not None


def test_oracle_relative_error_is_near_zero() -> None:
    horizon = 1
    issued, actuals, last, realized = _path()
    forecasts = [
        _forecast("oracle", stamp, horizon, quote)
        for stamp, quote in zip(issued, realized, strict=True)
    ]
    board = build_leaderboard(forecasts, actuals, NOW)
    cell = board["cells"][0]
    assert cell["model"] == "oracle"
    assert cell["n_scored"] == len(issued)
    assert cell["relative_error"] == 0.0
    assert cell["pinball"] == 0.0
    assert cell["directional_accuracy"] == 1.0
    naive = np.abs(np.log(last) - np.log(realized))
    assert relative_error(np.zeros_like(naive), naive) == 0.0


def test_future_targets_are_not_scored() -> None:
    issued = ISSUED0
    forecast = _forecast("clone", issued, 1, 100.0)
    actuals = {("BTC-USD", issued): 100.0, ("BTC-USD", issued + timedelta(hours=1)): 110.0}
    board = build_leaderboard([forecast], actuals, issued + timedelta(minutes=5))
    assert board["n_scored"] == 0
    assert board["n_pending"] == 1
    assert board["cells"] == []


def test_history_replaces_the_same_day_snapshot(tmp_path) -> None:
    horizon = 1
    issued, actuals, last, _realized = _path(n=4)
    forecasts = [
        _forecast("clone", stamp, horizon, quote) for stamp, quote in zip(issued, last, strict=True)
    ]
    first = build_leaderboard(forecasts, actuals, NOW)
    write_scores(tmp_path, first)
    assert leaderboard_path(tmp_path).is_file()
    write_history(tmp_path, first)
    write_history(tmp_path, first)
    payload = history_path(tmp_path).read_text(encoding="utf-8")
    assert payload.count(f'"date": "{first["as_of"]}"') == 1
    later = dict(first)
    later["generated_at"] = "2026-01-10T12:00:00Z"
    write_history(tmp_path, later)
    import json

    history = json.loads(history_path(tmp_path).read_text(encoding="utf-8"))
    assert len(history["snapshots"]) == 1
    assert history["snapshots"][0]["generated_at"] == "2026-01-10T12:00:00Z"


def test_write_scores_keeps_retrospective_label(tmp_path) -> None:
    import json

    horizon = 1
    issued, actuals, last, _realized = _path(n=4)
    forecasts = [
        _forecast("clone", stamp, horizon, quote) for stamp, quote in zip(issued, last, strict=True)
    ]
    attach_retrospective(
        tmp_path,
        {
            "rows": [],
            "cells": [],
            "n_forecasts": 0,
            "n_scored": 0,
            "n_pending": 0,
            "label": "live",
            "note": "should be rewritten",
        },
    )
    board = build_leaderboard(forecasts, actuals, NOW)
    write_scores(tmp_path, board)
    payload = json.loads(leaderboard_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["retrospective"]["label"] == "RETROSPECTIVE"
    assert payload["retrospective"]["live"] is False
    assert payload["retrospective"]["precommitted"] is False
    assert payload.get("label") != "RETROSPECTIVE"
