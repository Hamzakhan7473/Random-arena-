from __future__ import annotations

import math
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from arena.errors import ArenaError, InsufficientHistory, PublishedTooLate
from arena.ingest import DEFAULT_PRODUCTS, product_store_path, read_candles
from arena.models.registry import load_forecasters
from arena.publish import PublishResult, publish_forecasts
from arena.schema import HORIZON_HOURS, QUANTILE_KEYS, QUANTILES, Forecast
from arena.series import history_through, price_series
from arena.timeutil import as_utc_hour, ensure_utc, format_utc


def last_closed_issue_time(
    root: Path,
    products: list[str],
    now: datetime,
) -> datetime:
    """Issue time is the end of the newest fully closed hourly candle.

    Coinbase stores bucket starts. A candle is closed when start+1h <= now.
    The close is known at that end hour, which is the sealed issue_time.
    When products disagree, use the latest hour every product has closed.
    """
    clock = ensure_utc(now)
    ends: list[datetime] = []
    for product in products:
        candles = read_candles(product_store_path(root, product))
        if candles.empty:
            raise InsufficientHistory(f"no closed candles for {product}")
        stamps = pd.to_datetime(candles["timestamp_utc"], utc=True)
        closed = stamps[stamps + pd.Timedelta(hours=1) <= pd.Timestamp(clock)]
        if closed.empty:
            raise InsufficientHistory(f"no fully closed candle for {product}")
        last_start = closed.max().to_pydatetime()
        ends.append(as_utc_hour(last_start + timedelta(hours=1)))
    return min(ends)


def code_git_sha(root: Path) -> str:
    probe = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return "unknown"
    sha = probe.stdout.strip()
    return sha if sha else "unknown"


def log_price_history(root: Path, product: str, issue_time: datetime) -> pd.Series:
    prices = price_series(root, product, issue_time)
    series = history_through(prices, issue_time, min_points=2)
    log_prices = np.log(series.to_numpy(dtype=np.float64))
    if not np.isfinite(log_prices).all():
        raise InsufficientHistory(f"{product} log prices must be finite")
    return pd.Series(log_prices, index=series.index, dtype="float64")


def prices_from_log_quantiles(quoted: dict[float, float]) -> dict[str, float]:
    prices = {key: float(math.exp(quoted[level])) for key, level in zip(QUANTILE_KEYS, QUANTILES, strict=True)}
    ordered = [prices[key] for key in QUANTILE_KEYS]
    if any(ordered[i] > ordered[i + 1] for i in range(len(ordered) - 1)):
        ordered = sorted(ordered)
        prices = {key: ordered[i] for i, key in enumerate(QUANTILE_KEYS)}
    return prices


def run_forecasts(
    root: Path,
    *,
    products: list[str] | None = None,
    model_names: list[str] | None = None,
    issue_time: datetime | None = None,
    now: datetime | None = None,
) -> PublishResult:
    clock = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    chosen_products = list(DEFAULT_PRODUCTS if products is None else products)
    if not chosen_products:
        raise ArenaError("need at least one product")
    models = load_forecasters(model_names)
    issued = (
        as_utc_hour(issue_time)
        if issue_time is not None
        else last_closed_issue_time(root, chosen_products, clock)
    )
    earliest_target = issued + timedelta(hours=min(HORIZON_HOURS))
    if clock >= earliest_target:
        raise PublishedTooLate(
            f"publisher clock {clock.isoformat()} is not before target {earliest_target.isoformat()}"
        )
    sha = code_git_sha(root)
    batch: list[Forecast] = []
    for product in chosen_products:
        history = log_price_history(root, product, issued)
        for model in models:
            quoted = model.forecast(history, list(HORIZON_HOURS), list(QUANTILES))
            version = getattr(model, "version", "1")
            for horizon in HORIZON_HOURS:
                batch.append(
                    Forecast(
                        issue_time=issued,
                        target_time=issued + timedelta(hours=horizon),
                        model=model.name,
                        product=product,
                        horizon=horizon,
                        quantiles=prices_from_log_quantiles(quoted[horizon]),
                        model_version=str(version),
                        code_git_sha=sha,
                    )
                )
    return publish_forecasts(root, batch, now=clock)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from arena.cli import _extract_root, _symbols
    from arena.errors import ArenaError
    from arena.paths import find_root
    from arena.timeutil import parse_utc

    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        root_override, raw = _extract_root(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(prog="run_forecasts")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--product", action="append", default=None)
    parser.add_argument("--model", action="append", default=None)
    parser.add_argument("--issue-time", default=None)
    args = parser.parse_args(raw)
    root = (root_override or args.root)
    root = root.resolve() if root is not None else find_root()
    try:
        result = run_forecasts(
            root,
            products=_symbols(args.product),
            model_names=args.model,
            issue_time=parse_utc(args.issue_time) if args.issue_time else None,
        )
    except (ArenaError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    stamp = format_utc(result.issue_time)
    print(f"ISSUE_TIME={stamp}")
    print(f"sealed {result.rel}")
    print(result.digest)
    print(f"forecasts {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
