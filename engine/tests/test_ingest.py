from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pandas as pd
import pytest
import requests

from arena.errors import ActualConflict, IngestError
from arena.ingest import (
    COLUMNS,
    DEFAULT_PRODUCTS,
    GRANULARITY,
    MAX_CANDLES,
    fetch_hourly_candles,
    last_closed_start,
    read_candles,
    update_store,
)
from arena.timeutil import format_utc, parse_utc

NOW = datetime(2026, 1, 15, 17, 30, tzinfo=timezone.utc)
CLOSED = last_closed_start(NOW)  # 16:00 UTC


def _bucket(start: datetime, *, low=10.0, high=20.0, open_=12.0, close=18.0, volume=100.0) -> list:
    return [int(start.timestamp()), low, high, open_, close, volume]


def _range(start: datetime, hours: int) -> list[datetime]:
    return [start + timedelta(hours=offset) for offset in range(hours)]


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Serves Coinbase-shaped candles and rejects windows larger than 300 hours."""

    def __init__(self, candles: dict[datetime, list], *, fail_first=0, retry_after="0"):
        self.candles = candles
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.fail_first = fail_first
        self.retry_after = retry_after

    def get(self, url, params, headers, timeout):
        self.requests.append((url, dict(params)))
        if self.fail_first > 0:
            self.fail_first -= 1
            return FakeResponse(429, {"message": "Too Many Requests"}, {"Retry-After": self.retry_after})
        start = parse_utc(str(params["start"]))
        end = parse_utc(str(params["end"]))
        count = int((end - start).total_seconds() // GRANULARITY) + 1
        if count > MAX_CANDLES:
            return FakeResponse(400, {"message": "granularity too small for the requested time range"})
        rows = [row for stamp, row in self.candles.items() if start <= stamp <= end]
        rows.sort(key=lambda row: row[0], reverse=True)
        return FakeResponse(200, rows)


def _session_for(hours: list[datetime], **kwargs) -> FakeSession:
    return FakeSession({moment: _bucket(moment, close=100.0 + index) for index, moment in enumerate(hours)}, **kwargs)


def _assert_invariants(candles: pd.DataFrame, now: datetime = NOW) -> None:
    assert list(candles.columns) == list(COLUMNS)
    stamps = candles["timestamp_utc"]
    assert not stamps.duplicated().any()
    if len(candles) > 1:
        assert stamps.is_monotonic_increasing
        assert (stamps.diff().iloc[1:] > pd.Timedelta(0)).all()
    if not candles.empty:
        assert ((stamps + pd.Timedelta(hours=1)) <= pd.Timestamp(now)).all()


def test_maps_coinbase_low_high_open_close_volume_order() -> None:
    start = CLOSED - timedelta(hours=1)
    session = FakeSession({start: _bucket(start, low=10.0, high=20.0, open_=12.0, close=18.0, volume=7.5)})
    candles = fetch_hourly_candles("BTC-USD", start, start, now=NOW, session=session, sleeper=lambda _: None)
    _assert_invariants(candles)
    row = candles.iloc[0]
    assert row["open"] == 12.0
    assert row["high"] == 20.0
    assert row["low"] == 10.0
    assert row["close"] == 18.0
    assert row["volume"] == 7.5
    assert pd.Timestamp(row["timestamp_utc"]).to_pydatetime() == start


def test_pages_so_no_request_exceeds_300_candles() -> None:
    start = CLOSED - timedelta(hours=300)
    hours = _range(start, 301)
    session = _session_for(hours)
    candles = fetch_hourly_candles("ETH-USD", start, CLOSED, now=NOW, session=session, sleeper=lambda _: None)
    assert len(session.requests) == 2
    for _, params in session.requests:
        window_start = parse_utc(str(params["start"]))
        window_end = parse_utc(str(params["end"]))
        count = int((window_end - window_start).total_seconds() // GRANULARITY) + 1
        assert count <= MAX_CANDLES
        assert params["granularity"] == GRANULARITY or int(params["granularity"]) == GRANULARITY
    assert len(candles) == 301
    _assert_invariants(candles)


def test_retries_429_then_succeeds() -> None:
    sleeps: list[float] = []
    session = _session_for([CLOSED], fail_first=1, retry_after="0")
    candles = fetch_hourly_candles(
        "SOL-USD",
        CLOSED,
        CLOSED,
        now=NOW,
        session=session,
        sleeper=sleeps.append,
    )
    assert len(session.requests) == 2
    assert session.requests[0][0].endswith("/products/SOL-USD/candles")
    assert sleeps
    assert list(candles["close"]) == [100.0]
    _assert_invariants(candles)


def test_drops_the_currently_forming_candle_even_if_the_api_returns_it() -> None:
    forming = datetime(2026, 1, 15, 17, tzinfo=timezone.utc)
    session = FakeSession(
        {
            CLOSED: _bucket(CLOSED, close=42.0),
            forming: _bucket(forming, close=99.0),
        }
    )
    candles = fetch_hourly_candles(
        "BTC-USD",
        CLOSED,
        forming,
        now=NOW,
        session=session,
        sleeper=lambda _: None,
    )
    assert list(candles["close"]) == [42.0]
    _assert_invariants(candles)
    assert (candles["timestamp_utc"] + pd.Timedelta(hours=1) <= pd.Timestamp(NOW)).all()


def test_update_store_appends_only_new_closed_candles(tmp_path) -> None:
    first = CLOSED - timedelta(hours=2)
    hours = _range(first, 3)  # 14:00, 15:00, 16:00; 17:00 is forming
    forming = datetime(2026, 1, 15, 17, tzinfo=timezone.utc)
    session = FakeSession(
        {moment: _bucket(moment, close=200.0 + index) for index, moment in enumerate(hours + [forming])}
    )
    written = update_store(
        ["BTC-USD"],
        root=tmp_path,
        now=NOW,
        session=session,
        lookback_hours=3,
        sleeper=lambda _: None,
    )
    assert written == {"BTC-USD": 3}
    path = tmp_path / "data" / "actuals" / "BTC-USD.parquet"
    stored = read_candles(path)
    _assert_invariants(stored)
    assert list(stored["timestamp_utc"]) == list(pd.to_datetime(hours, utc=True))

    again = update_store(
        ["BTC-USD"],
        root=tmp_path,
        now=NOW,
        session=session,
        lookback_hours=3,
        sleeper=lambda _: None,
    )
    assert again == {"BTC-USD": 0}
    rerun = read_candles(path)
    _assert_invariants(rerun)
    assert len(rerun) == 3
    assert not rerun["timestamp_utc"].duplicated().any()
    assert rerun["timestamp_utc"].is_monotonic_increasing
    assert ((rerun["timestamp_utc"] + pd.Timedelta(hours=1)) <= pd.Timestamp(NOW)).all()


def test_update_store_refuses_a_changed_close(tmp_path) -> None:
    session = _session_for([CLOSED])
    update_store(["BTC-USD"], root=tmp_path, now=NOW, session=session, lookback_hours=1, sleeper=lambda _: None)
    conflict = FakeSession({CLOSED: _bucket(CLOSED, close=999.0)})
    with pytest.raises(ActualConflict):
        update_store(
            ["BTC-USD"],
            root=tmp_path,
            now=NOW,
            session=conflict,
            lookback_hours=1,
            sleeper=lambda _: None,
        )


def test_update_store_defaults_to_three_products(tmp_path) -> None:
    session = _session_for([CLOSED])
    written = update_store(root=tmp_path, now=NOW, session=session, lookback_hours=1, sleeper=lambda _: None)
    assert list(written) == list(DEFAULT_PRODUCTS)
    products = [urlparse(url).path.split("/")[-2] for url, _ in session.requests]
    assert products == list(DEFAULT_PRODUCTS)
    for product in DEFAULT_PRODUCTS:
        candles = read_candles(tmp_path / "data" / "actuals" / f"{product}.parquet")
        _assert_invariants(candles)
        assert len(candles) == 1


def test_rejects_an_inverted_window() -> None:
    with pytest.raises(IngestError, match="ends before"):
        fetch_hourly_candles("BTC-USD", CLOSED, CLOSED - timedelta(hours=1), now=NOW, sleeper=lambda _: None)


def test_network_errors_exhaust_retries() -> None:
    class Boom(requests.Session):
        def get(self, url, params, headers, timeout):
            raise requests.ConnectionError("down")

    with pytest.raises(IngestError):
        fetch_hourly_candles(
            "BTC-USD",
            CLOSED,
            CLOSED,
            now=NOW,
            session=Boom(),
            sleeper=lambda _: None,
        )


def test_query_uses_iso_start_end_and_hourly_granularity() -> None:
    session = _session_for([CLOSED])
    fetch_hourly_candles("BTC-USD", CLOSED, CLOSED, now=NOW, session=session, sleeper=lambda _: None)
    _, params = session.requests[0]
    assert int(params["granularity"]) == GRANULARITY
    assert params["start"] == format_utc(CLOSED)
    assert params["end"] == format_utc(CLOSED)
