from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from arena.errors import ActualConflict, IngestError
from arena.paths import find_root
from arena.timeutil import ensure_utc, floor_hour, format_utc

# Coinbase Exchange public REST, current docs:
# https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
# GET {EXCHANGE_API}/products/{product_id}/candles
# granularity must be one of {60, 300, 900, 3600, 21600, 86400}
# start/end are a pair; if either is omitted, both are ignored
# each row is [time, low, high, open, close, volume]; time is the bucket start (unix seconds)
# a request is rejected above 300 candles
#
# Public rate limit (https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits):
# 10 requests/sec per IP, bursts up to 15, HTTP 429 when exceeded.
EXCHANGE_API = "https://api.exchange.coinbase.com"
CANDLES_PATH = "/products/{product_id}/candles"
GRANULARITY = 3600
MAX_CANDLES = 300
PUBLIC_MIN_INTERVAL = 0.12
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 6
DEFAULT_PRODUCTS: tuple[str, ...] = ("BTC-USD", "ETH-USD", "SOL-USD")
DEFAULT_LOOKBACK_HOURS = 240
COLUMNS: tuple[str, ...] = ("timestamp_utc", "open", "high", "low", "close", "volume")
USER_AGENT = "random-walk-arena"


def empty_candles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp_utc": pd.Series(dtype="datetime64[ns, UTC]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
        }
    )


def product_store_path(root: Path, product_id: str) -> Path:
    if not product_id or any(part in product_id for part in ("/", "\\", "..")):
        raise IngestError(f"invalid product id: {product_id!r}")
    return root / "data" / "actuals" / f"{product_id}.parquet"


def last_closed_start(now: datetime) -> datetime:
    """Bucket start of the newest hourly candle whose close is already known."""
    forming = floor_hour(ensure_utc(now))
    return forming - timedelta(hours=1)


def is_closed(timestamp_utc: datetime, now: datetime) -> bool:
    return ensure_utc(timestamp_utc) + timedelta(hours=1) <= ensure_utc(now)


def fetch_hourly_candles(
    product_id: str,
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    session: requests.Session | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Return closed hourly candles in [start, end], inclusive, oldest first."""
    start = floor_hour(start)
    end = floor_hour(end)
    clock = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    if end < start:
        raise IngestError("candle window ends before it starts")
    last_closed = last_closed_start(clock)
    if start > last_closed:
        return empty_candles()
    end = min(end, last_closed)

    client = session or requests.Session()
    frames: list[pd.DataFrame] = []
    cursor = start
    last_request_at = 0.0
    while cursor <= end:
        window_end = min(end, cursor + timedelta(seconds=GRANULARITY * (MAX_CANDLES - 1)))
        last_request_at = _throttle(last_request_at, sleeper)
        payload = _get_candles(client, product_id, cursor, window_end, sleeper)
        frame = _candles_from_payload(payload)
        in_range = _slice_window(frame, cursor, window_end)
        frames.append(in_range)
        if in_range.empty:
            cursor = window_end + timedelta(hours=1)
        else:
            cursor = _as_utc_datetime(in_range["timestamp_utc"].iloc[-1]) + timedelta(hours=1)
    if not frames:
        return empty_candles()
    candles = _finalize(pd.concat(frames, ignore_index=True), clock)
    return _slice_window(candles, start, end)


def update_store(
    product_ids: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    now: datetime | None = None,
    session: requests.Session | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Append only new fully closed hourly candles to data/actuals/{product}.parquet."""
    if lookback_hours < 1:
        raise IngestError("lookback-hours must be at least 1")
    products = list(DEFAULT_PRODUCTS if product_ids is None else product_ids)
    if not products:
        raise IngestError("no product ids")
    repo = root.resolve() if root is not None else find_root()
    clock = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    client = session or requests.Session()
    end = last_closed_start(clock)
    written: dict[str, int] = {}
    for product_id in products:
        path = product_store_path(repo, product_id)
        stored = read_candles(path)
        start = _next_start(stored, end, lookback_hours)
        if start is None:
            written[product_id] = 0
            continue
        incoming = fetch_hourly_candles(
            product_id,
            start,
            end,
            now=clock,
            session=client,
            sleeper=sleeper,
        )
        added = _append_closed(stored, incoming, clock)
        if added.empty:
            written[product_id] = 0
            continue
        combined = added if stored.empty else pd.concat([stored, added], ignore_index=True)
        write_candles(path, _finalize(combined, clock))
        written[product_id] = int(len(added))
    return written


def read_candles(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return empty_candles()
    try:
        frame = pd.read_parquet(path, engine="pyarrow")
    except Exception as exc:
        raise IngestError(f"could not read {path.name}") from exc
    return _finalize(frame, datetime.now(timezone.utc), require_closed=False)


def write_candles(path: Path, candles: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    candles.to_parquet(temporary, index=False, engine="pyarrow")
    os.replace(temporary, path)


def _next_start(stored: pd.DataFrame, end: datetime, lookback_hours: int) -> datetime | None:
    if stored.empty:
        return end - timedelta(hours=lookback_hours - 1)
    last = _as_utc_datetime(stored["timestamp_utc"].iloc[-1])
    if last > end:
        return None
    return last


def _append_closed(stored: pd.DataFrame, incoming: pd.DataFrame, now: datetime) -> pd.DataFrame:
    incoming = _finalize(incoming, now)
    if incoming.empty:
        return empty_candles()
    if stored.empty:
        return incoming
    previous = stored.set_index("timestamp_utc")
    fresh_rows = []
    for row in incoming.itertuples(index=False):
        stamp = pd.Timestamp(row.timestamp_utc)
        if stamp in previous.index:
            _refuse_rewrite(previous.loc[stamp], row)
            continue
        fresh_rows.append(row)
    if not fresh_rows:
        return empty_candles()
    return pd.DataFrame(fresh_rows, columns=COLUMNS)


def _refuse_rewrite(existing: pd.Series | pd.DataFrame, incoming) -> None:
    current = existing.iloc[0] if isinstance(existing, pd.DataFrame) else existing
    for column in ("open", "high", "low", "close", "volume"):
        old = float(current[column])
        new = float(getattr(incoming, column))
        if abs(old - new) > 1e-12:
            raise ActualConflict(
                f"{column} at {incoming.timestamp_utc} is already {old}, refusing {new}"
            )


def _get_candles(
    client: requests.Session,
    product_id: str,
    start: datetime,
    end: datetime,
    sleeper: Callable[[float], None],
) -> object:
    url = f"{EXCHANGE_API}{CANDLES_PATH.format(product_id=product_id)}"
    params = {
        "granularity": GRANULARITY,
        "start": format_utc(start),
        "end": format_utc(end),
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_error = "coinbase request failed"
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.get(url, params=params, headers=headers, timeout=30)
        except requests.RequestException as exc:
            last_error = str(exc)
            _backoff(attempt, None, sleeper)
            continue
        if response.status_code in RETRY_STATUSES:
            last_error = f"coinbase returned {response.status_code}"
            _backoff(attempt, response.headers.get("Retry-After"), sleeper)
            continue
        if response.status_code != 200:
            raise IngestError(_coinbase_message(response, f"coinbase returned {response.status_code}"))
        try:
            return response.json()
        except ValueError as exc:
            raise IngestError("coinbase returned non-json") from exc
    raise IngestError(last_error)


def _candles_from_payload(payload: object) -> pd.DataFrame:
    if isinstance(payload, dict) and "message" in payload:
        raise IngestError(f"coinbase: {payload['message']}")
    if not isinstance(payload, list):
        raise IngestError("candle payload is not a list")
    rows: list[dict[str, object]] = []
    for row in payload:
        parsed = _parse_bucket(row)
        if parsed is not None:
            rows.append(parsed)
    if not rows:
        return empty_candles()
    return pd.DataFrame(rows, columns=list(COLUMNS))


def _parse_bucket(row: object) -> dict[str, object] | None:
    # Docs: [time, low, high, open, close, volume]. Short form omits volume.
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        raise IngestError("candle row is missing fields")
    try:
        timestamp = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
        low = float(row[1])
        high = float(row[2])
        open_price = float(row[3])
        close = float(row[4])
        volume = float(row[5]) if len(row) > 5 else 0.0
    except (TypeError, ValueError, OSError) as exc:
        raise IngestError("candle is not an hourly UTC bucket") from exc
    if timestamp != floor_hour(timestamp):
        raise IngestError("candle timestamp is not an exact UTC hour")
    prices = (open_price, high, low, close)
    if not all(value > 0 and pd.notna(value) for value in prices):
        return None
    if not (volume >= 0 and pd.notna(volume)):
        return None
    return {
        "timestamp_utc": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _finalize(frame: pd.DataFrame, now: datetime, *, require_closed: bool = True) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_candles()
    missing = [column for column in COLUMNS if column not in frame.columns]
    if missing:
        raise IngestError(f"candle frame missing columns: {missing}")
    candles = frame.loc[:, list(COLUMNS)].copy()
    stamps = pd.to_datetime(candles["timestamp_utc"], utc=True)
    candles["timestamp_utc"] = stamps
    for column in ("open", "high", "low", "close", "volume"):
        candles[column] = pd.to_numeric(candles[column], errors="raise").astype("float64")
    candles = candles.sort_values("timestamp_utc", kind="mergesort")
    candles = candles.drop_duplicates(subset=["timestamp_utc"], keep="last")
    if require_closed:
        cutoff = pd.Timestamp(ensure_utc(now))
        candles = candles[candles["timestamp_utc"] + pd.Timedelta(hours=1) <= cutoff]
    candles = candles.reset_index(drop=True)
    _assert_invariants(candles, now, require_closed=require_closed)
    return candles


def _assert_invariants(candles: pd.DataFrame, now: datetime, *, require_closed: bool) -> None:
    if candles.empty:
        return
    stamps = candles["timestamp_utc"]
    if stamps.duplicated().any():
        raise IngestError("duplicate candle timestamps")
    if not stamps.is_monotonic_increasing:
        raise IngestError("candle timestamps are not strictly increasing")
    if len(candles) > 1:
        deltas = stamps.diff().iloc[1:]
        if not (deltas > pd.Timedelta(0)).all():
            raise IngestError("candle timestamps are not strictly increasing")
    if require_closed:
        cutoff = pd.Timestamp(ensure_utc(now))
        if not (stamps + pd.Timedelta(hours=1) <= cutoff).all():
            raise IngestError("refusing a candle that is still forming")


def _slice_window(frame: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    if frame.empty:
        return empty_candles()
    stamps = pd.to_datetime(frame["timestamp_utc"], utc=True)
    mask = (stamps >= pd.Timestamp(start)) & (stamps <= pd.Timestamp(end))
    sliced = frame.loc[mask].copy()
    sliced["timestamp_utc"] = stamps.loc[mask]
    return sliced.sort_values("timestamp_utc", kind="mergesort").reset_index(drop=True)


def _throttle(last_request_at: float, sleeper: Callable[[float], None]) -> float:
    if last_request_at:
        wait = PUBLIC_MIN_INTERVAL - (time.monotonic() - last_request_at)
        if wait > 0:
            sleeper(wait)
    return time.monotonic()


def _backoff(attempt: int, retry_after: str | None, sleeper: Callable[[float], None]) -> None:
    if attempt >= MAX_ATTEMPTS - 1:
        return
    delay = min(8.0, 0.5 * (2**attempt))
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass
    sleeper(delay)


def _coinbase_message(response: requests.Response, fallback: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        return fallback
    if isinstance(payload, dict) and payload.get("message"):
        return f"coinbase: {payload['message']}"
    return fallback


def _as_utc_datetime(value: object) -> datetime:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.to_pydatetime()
