from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import binomtest, norm

from arena.actuals import load_observations
from arena.paths import history_path, leaderboard_path
from arena.schema import HORIZON_LABEL, HORIZONS, QUANTILE_KEYS, QUANTILES, Forecast
from arena.store import load_all_forecasts
from arena.timeutil import ensure_utc

NOCHANGE = "no-change"
BOOTSTRAP_BLOCK = 24
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 0
SPA_REPS = 1000
SPA_ALPHA = 0.05
COVERAGE_LO = "0.1"
COVERAGE_HI = "0.9"
METRIC = "mase_median_vs_nochange"
RETROSPECTIVE_LABEL = "RETROSPECTIVE"
LIVE_NOTE = (
    "No winner is declared. beats_random_walk is true only when Hansen SPA "
    "rejects NoChange for that model at 5%."
)
RETROSPECTIVE_NOTE = (
    "RETROSPECTIVE walk-forward after published training cutoffs. "
    "Not live. Not precommitted."
)


@dataclass(frozen=True)
class Scored:
    model: str
    product: str
    horizon: int
    issue_time: datetime
    target_time: datetime
    actual_log: float
    last_log: float
    median_log: float
    q_lo_log: float
    q_hi_log: float
    pinball: float


def pinball(actual: float, predicted: float, level: float) -> float:
    error = actual - predicted
    if error >= 0:
        return level * error
    return (level - 1.0) * error


def mean_pinball_log_price(actual: float, quantiles: dict[str, float]) -> float:
    observed = math.log(actual)
    losses = [
        pinball(observed, math.log(quantiles[key]), level)
        for key, level in zip(QUANTILE_KEYS, QUANTILES, strict=True)
    ]
    return sum(losses) / len(losses)


def _round(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 10)


def _log_price(price: float) -> float:
    if price <= 0 or not math.isfinite(price):
        raise ValueError("price must be finite and positive")
    return math.log(price)


def join_scored(
    forecasts: list[Forecast],
    actuals: dict[tuple[str, datetime], float],
    now: datetime,
) -> tuple[list[Scored], int, int]:
    """Join sealed forecasts to realized log prices at target_time.

    A forecast is scored only when ``target_time <= now`` and both the
    issue-time close (NoChange median / last price) and the target close exist.
    """
    now = ensure_utc(now)
    scored: list[Scored] = []
    pending = 0
    for forecast in forecasts:
        if forecast.target_time > now:
            pending += 1
            continue
        actual_price = actuals.get((forecast.product, forecast.target_time))
        last_price = actuals.get((forecast.product, forecast.issue_time))
        if actual_price is None or last_price is None:
            pending += 1
            continue
        median = forecast.quantiles["0.5"]
        scored.append(
            Scored(
                model=forecast.model,
                product=forecast.product,
                horizon=forecast.horizon,
                issue_time=forecast.issue_time,
                target_time=forecast.target_time,
                actual_log=_log_price(actual_price),
                last_log=_log_price(last_price),
                median_log=_log_price(median),
                q_lo_log=_log_price(forecast.quantiles[COVERAGE_LO]),
                q_hi_log=_log_price(forecast.quantiles[COVERAGE_HI]),
                pinball=mean_pinball_log_price(actual_price, forecast.quantiles),
            )
        )
    return scored, len(scored), pending


def relative_error(model_abs: np.ndarray, naive_abs: np.ndarray) -> float | None:
    """MASE of the median versus NoChange: mean|e| / mean|e_nochange|."""
    if model_abs.size == 0:
        return None
    denom = float(np.mean(naive_abs))
    if denom == 0.0:
        numer = float(np.mean(model_abs))
        return 0.0 if numer == 0.0 else None
    return float(np.mean(model_abs) / denom)


def newey_west_mean_se(values: np.ndarray, lag: int) -> float:
    """HAC standard error of the mean, Bartlett kernel, bandwidth ``lag``."""
    n = int(values.size)
    if n < 2:
        return 0.0
    centered = values - float(np.mean(values))
    gamma0 = float(np.dot(centered, centered) / n)
    var = gamma0
    max_lag = min(max(int(lag), 0), n - 1)
    for j in range(1, max_lag + 1):
        gamma = float(np.dot(centered[j:], centered[:-j]) / n)
        weight = 1.0 - j / (max_lag + 1)
        var += 2.0 * weight * gamma
    var = max(var, 0.0)
    return math.sqrt(var / n)


def diebold_mariano(model_sq: np.ndarray, naive_sq: np.ndarray, horizon: int) -> tuple[float | None, float | None]:
    """DM test of squared log-error versus NoChange, Newey–West lag = h-1."""
    if model_sq.size < 2:
        return None, None
    diff = model_sq - naive_sq
    mean = float(np.mean(diff))
    se = newey_west_mean_se(diff, lag=horizon - 1)
    if se == 0.0:
        return (0.0, 1.0) if mean == 0.0 else (None, None)
    stat = mean / se
    pvalue = float(2.0 * norm.sf(abs(stat)))
    return stat, pvalue


def moving_block_relative_ci(
    model_abs: np.ndarray,
    naive_abs: np.ndarray,
    *,
    block: int = BOOTSTRAP_BLOCK,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """Moving-block bootstrap 95% CI for MASE (relative error)."""
    n = int(model_abs.size)
    if n == 0:
        return None, None
    length = max(1, min(int(block), n))
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / length))
    max_start = n - length + 1
    ratios = np.empty(reps, dtype=np.float64)
    for i in range(reps):
        starts = rng.integers(0, max_start, size=n_blocks)
        index = np.concatenate([np.arange(int(start), int(start) + length) for start in starts])[:n]
        denom = float(np.mean(naive_abs[index]))
        numer = float(np.mean(model_abs[index]))
        if denom == 0.0:
            ratios[i] = 0.0 if numer == 0.0 else np.nan
        else:
            ratios[i] = numer / denom
    finite = ratios[np.isfinite(ratios)]
    if finite.size == 0:
        return None, None
    lo, hi = np.quantile(finite, [0.025, 0.975])
    return float(lo), float(hi)


def directional_accuracy(median_log: np.ndarray, last_log: np.ndarray, actual_log: np.ndarray) -> tuple[float | None, float | None, int]:
    """Share of correct median directions versus the issue-time log price."""
    forecast_move = median_log - last_log
    realized_move = actual_log - last_log
    decided = (forecast_move != 0.0) & (realized_move != 0.0)
    n = int(np.count_nonzero(decided))
    if n == 0:
        return None, None, 0
    hits = int(np.count_nonzero(np.sign(forecast_move[decided]) == np.sign(realized_move[decided])))
    accuracy = hits / n
    pvalue = float(binomtest(hits, n, 0.5, alternative="two-sided").pvalue)
    return accuracy, pvalue, n


def coverage_80(actual_log: np.ndarray, q_lo: np.ndarray, q_hi: np.ndarray) -> float | None:
    if actual_log.size == 0:
        return None
    inside = (actual_log >= q_lo) & (actual_log <= q_hi)
    return float(np.mean(inside))


def spa_vs_nochange(
    losses: dict[str, np.ndarray],
    naive: np.ndarray,
    *,
    block: int = BOOTSTRAP_BLOCK,
    reps: int = SPA_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, frozenset[str]]:
    """Hansen SPA with NoChange as benchmark. Returns consistent p-value and superior models.

    Null: no alternative has lower expected loss than NoChange. ``beats_random_walk``
    is set only for models ``arch.bootstrap.SPA.better_models`` rejects at 5%.
    """
    names = [
        name
        for name, series in losses.items()
        if name != NOCHANGE and not np.allclose(series, naive)
    ]
    if not names or naive.size < 3:
        return None, frozenset()
    stacked = np.column_stack([losses[name] for name in names])
    if stacked.shape[0] < 3 or stacked.shape[1] < 1:
        return None, frozenset()
    from arch.bootstrap import SPA

    block_size = max(1, min(int(block), int(naive.size)))
    spa = SPA(
        naive,
        stacked,
        block_size=block_size,
        reps=int(reps),
        bootstrap="moving block",
        seed=int(seed),
    )
    spa.compute()
    pvalue = float(spa.pvalues["consistent"])
    better = {names[int(index)] for index in spa.better_models(pvalue=SPA_ALPHA, pvalue_type="consistent")}
    return pvalue, frozenset(better)


def _cell_metrics(rows: list[Scored], horizon: int) -> dict:
    actual = np.array([row.actual_log for row in rows], dtype=np.float64)
    last = np.array([row.last_log for row in rows], dtype=np.float64)
    median = np.array([row.median_log for row in rows], dtype=np.float64)
    q_lo = np.array([row.q_lo_log for row in rows], dtype=np.float64)
    q_hi = np.array([row.q_hi_log for row in rows], dtype=np.float64)
    pinballs = np.array([row.pinball for row in rows], dtype=np.float64)
    model_abs = np.abs(median - actual)
    naive_abs = np.abs(last - actual)
    model_sq = (median - actual) ** 2
    naive_sq = (last - actual) ** 2
    mase = relative_error(model_abs, naive_abs)
    ci_lo, ci_hi = moving_block_relative_ci(model_abs, naive_abs)
    dm_stat, dm_p = diebold_mariano(model_sq, naive_sq, horizon)
    hit_rate, dir_p, n_dir = directional_accuracy(median, last, actual)
    return {
        "n_scored": len(rows),
        "relative_error": _round(mase),
        "mase": _round(mase),
        "relative_error_ci": [_round(ci_lo), _round(ci_hi)] if ci_lo is not None and ci_hi is not None else None,
        "pinball": _round(float(np.mean(pinballs))),
        "directional_accuracy": _round(hit_rate),
        "directional_pvalue": _round(dir_p),
        "n_directional": n_dir,
        "coverage_80": _round(coverage_80(actual, q_lo, q_hi)),
        "dm_statistic": _round(dm_stat),
        "dm_pvalue": _round(dm_p),
        "beats_random_walk": False,
        "squared_error": model_sq,
        "naive_squared_error": naive_sq,
        "times": [row.target_time for row in rows],
    }


def _align_losses(cells: dict[str, dict]) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    if not cells:
        return None
    clock: set[datetime] | None = None
    for metrics in cells.values():
        stamps = set(metrics["times"])
        clock = stamps if clock is None else clock & stamps
    if not clock or len(clock) < 3:
        return None
    ordered = sorted(clock)
    naive = None
    losses: dict[str, np.ndarray] = {}
    for model, metrics in cells.items():
        by_time = {stamp: float(err) for stamp, err in zip(metrics["times"], metrics["squared_error"], strict=True)}
        naive_by_time = {
            stamp: float(err) for stamp, err in zip(metrics["times"], metrics["naive_squared_error"], strict=True)
        }
        series = np.array([by_time[stamp] for stamp in ordered], dtype=np.float64)
        losses[model] = series
        if naive is None:
            naive = np.array([naive_by_time[stamp] for stamp in ordered], dtype=np.float64)
    if naive is None:
        return None
    return naive, losses


def build_leaderboard(
    forecasts: list[Forecast],
    actuals: dict[tuple[str, datetime], float],
    now: datetime,
) -> dict:
    now = ensure_utc(now)
    joined, n_scored, n_pending = join_scored(forecasts, actuals, now)
    grouped: dict[tuple[str, str, int], list[Scored]] = defaultdict(list)
    for row in joined:
        grouped[(row.model, row.product, row.horizon)].append(row)
    for key in grouped:
        grouped[key].sort(key=lambda row: row.target_time)

    raw_cells: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for (model, product, horizon), rows in grouped.items():
        raw_cells[(product, horizon)][model] = _cell_metrics(rows, horizon)

    spa_report: dict[str, dict[str, dict]] = {}
    for (product, horizon), models in raw_cells.items():
        aligned = _align_losses(models)
        pvalue, better = (None, frozenset())
        n_spa = 0
        if aligned is not None:
            naive, losses = aligned
            n_spa = int(naive.size)
            pvalue, better = spa_vs_nochange(losses, naive)
        label = HORIZON_LABEL[horizon]
        spa_report.setdefault(product, {})[label] = {
            "pvalue": _round(pvalue),
            "n_scored": n_spa,
            "better_models": sorted(better),
        }
        for model, metrics in models.items():
            metrics["beats_random_walk"] = model != NOCHANGE and model in better
            metrics["spa_pvalue"] = _round(pvalue)

    cells = []
    for (product, horizon), models in sorted(raw_cells.items(), key=lambda item: (item[0][0], item[0][1])):
        for model in sorted(models):
            metrics = models[model]
            cells.append(
                {
                    "model": model,
                    "product": product,
                    "horizon": horizon,
                    "n_scored": metrics["n_scored"],
                    "relative_error": metrics["relative_error"],
                    "mase": metrics["mase"],
                    "relative_error_ci": metrics["relative_error_ci"],
                    "pinball": metrics["pinball"],
                    "directional_accuracy": metrics["directional_accuracy"],
                    "directional_pvalue": metrics["directional_pvalue"],
                    "n_directional": metrics["n_directional"],
                    "coverage_80": metrics["coverage_80"],
                    "dm_statistic": metrics["dm_statistic"],
                    "dm_pvalue": metrics["dm_pvalue"],
                    "spa_pvalue": metrics["spa_pvalue"],
                    "beats_random_walk": bool(metrics["beats_random_walk"]),
                }
            )

    rows = _aggregate_rows(cells)
    live_since = None
    if joined:
        live_since = min(row.issue_time for row in joined).strftime("%Y-%m-%d")
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": now.strftime("%Y-%m-%d"),
        "live_since": live_since,
        "metric": METRIC,
        "note": LIVE_NOTE,
        "quantiles": list(QUANTILES),
        "horizons": list(HORIZONS),
        "n_forecasts": len(forecasts),
        "n_scored": n_scored,
        "n_pending": n_pending,
        "spa": spa_report,
        "cells": cells,
        "rows": rows,
    }


def _aggregate_rows(cells: list[dict]) -> list[dict]:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for cell in cells:
        by_model[cell["model"]].append(cell)
    rows = []
    for model in sorted(by_model):
        group = by_model[model]
        n = sum(cell["n_scored"] for cell in group)
        by_horizon = {}
        for horizon in HORIZONS:
            subset = [cell for cell in group if HORIZON_LABEL[cell["horizon"]] == horizon]
            by_horizon[horizon] = {
                "relative_error": _weighted_mean(subset, "relative_error"),
                "relative_error_ci": _weighted_ci(subset),
                "pinball": _weighted_mean(subset, "pinball"),
                "coverage_80": _weighted_mean(subset, "coverage_80"),
                "directional_accuracy": _weighted_mean(subset, "directional_accuracy"),
                "dm_pvalue": _weighted_mean(subset, "dm_pvalue"),
                "n_scored": sum(cell["n_scored"] for cell in subset),
                "n": sum(cell["n_scored"] for cell in subset),
                "beats_random_walk": any(cell["beats_random_walk"] for cell in subset),
            }
        rows.append(
            {
                "model": model,
                "model_id": model,
                "n_scored": n,
                "relative_error": _weighted_mean(group, "relative_error"),
                "pinball": _weighted_mean(group, "pinball"),
                "beats_random_walk": any(cell["beats_random_walk"] for cell in group),
                "by_horizon": by_horizon,
            }
        )
    return rows


def _weighted_ci(cells: list[dict]) -> list[float | None] | None:
    total_lo = 0.0
    total_hi = 0.0
    weight = 0
    for cell in cells:
        ci = cell.get("relative_error_ci")
        n = int(cell["n_scored"])
        if not isinstance(ci, (list, tuple)) or len(ci) != 2 or n == 0:
            continue
        lo, hi = ci[0], ci[1]
        if lo is None or hi is None:
            continue
        total_lo += float(lo) * n
        total_hi += float(hi) * n
        weight += n
    if weight == 0:
        return None
    return [_round(total_lo / weight), _round(total_hi / weight)]


def _weighted_mean(cells: list[dict], field: str) -> float | None:
    total = 0.0
    weight = 0
    for cell in cells:
        value = cell.get(field)
        n = int(cell["n_scored"])
        if value is None or n == 0:
            continue
        total += float(value) * n
        weight += n
    if weight == 0:
        return None
    return _round(total / weight)


def score_repo(root: Path, *, now: datetime | None = None) -> dict:
    moment = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    forecasts = load_all_forecasts(root)
    actuals = {(row.symbol, row.time): row.close for row in load_observations(root)}
    return build_leaderboard(forecasts, actuals, moment)


def empty_live_board() -> dict:
    return {
        "generated_at": None,
        "as_of": None,
        "metric": METRIC,
        "note": LIVE_NOTE,
        "quantiles": list(QUANTILES),
        "horizons": list(HORIZONS),
        "n_forecasts": 0,
        "n_scored": 0,
        "n_pending": 0,
        "live_since": None,
        "spa": {},
        "cells": [],
        "rows": [],
    }


def label_retrospective(board: dict) -> dict:
    """Force the walk-forward board to read as RETROSPECTIVE, never live."""
    payload = dict(board)
    payload["label"] = RETROSPECTIVE_LABEL
    payload["kind"] = "retrospective"
    payload["live"] = False
    payload["precommitted"] = False
    payload["note"] = RETROSPECTIVE_NOTE
    return payload


def write_leaderboard(root: Path, board: dict) -> Path:
    path = leaderboard_path(root)
    payload = dict(board)
    if "retrospective" not in payload:
        existing = _read_json(path)
        stored = existing.get("retrospective") if isinstance(existing, dict) else None
        if isinstance(stored, dict):
            payload["retrospective"] = label_retrospective(stored)
    elif isinstance(payload.get("retrospective"), dict):
        payload["retrospective"] = label_retrospective(payload["retrospective"])
    _write_json(path, payload)
    return path


def attach_retrospective(root: Path, board: dict) -> Path:
    """Write the RETROSPECTIVE section without touching live rows or history."""
    path = leaderboard_path(root)
    live = _read_json(path)
    if not isinstance(live, dict) or "rows" not in live:
        live = empty_live_board()
    live["retrospective"] = label_retrospective(board)
    _write_json(path, live)
    return path


def write_history(root: Path, board: dict) -> Path:
    path = history_path(root)
    existing = {"snapshots": []}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("snapshots"), list):
            existing = loaded
    date = str(board.get("as_of") or "")
    snapshot = {
        "date": date,
        "generated_at": board.get("generated_at"),
        "n_scored": board.get("n_scored"),
        "n_pending": board.get("n_pending"),
        "spa": board.get("spa"),
        "cells": board.get("cells"),
    }
    snapshots = [row for row in existing["snapshots"] if row.get("date") != date]
    snapshots.append(snapshot)
    snapshots.sort(key=lambda row: str(row.get("date") or ""))
    _write_json(path, {"snapshots": snapshots})
    return path


def write_scores(root: Path, board: dict) -> tuple[Path, Path]:
    return write_leaderboard(root, board), write_history(root, board)


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
