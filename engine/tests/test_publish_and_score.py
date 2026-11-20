from datetime import datetime, timedelta, timezone

import pytest

from arena.actuals import append_observations
from arena.errors import ActualConflict, ForecastImmutable, PublishedTooLate
from arena.manifest import sha256_bytes
from arena.paths import manifest_path
from arena.run_forecasts import run_forecasts
from arena.schema import Observation
from arena.score import pinball
from arena.store import load_forecast_file
from arena.timeutil import format_utc
from arena.verify import verify

ISSUED = datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
NOW = ISSUED + timedelta(minutes=5)


def _seed(root, issued=ISSUED, close=100.0, hours=48, symbol="BTC-USD") -> None:
    rows = [
        Observation(symbol=symbol, time=issued - timedelta(hours=hours - 1 - index), close=close)
        for index in range(hours)
    ]
    append_observations(root, rows)


def _publish_no_change(root, issued=ISSUED, now=NOW, close=100.0):
    _seed(root, issued=issued, close=close)
    return run_forecasts(
        root,
        products=["BTC-USD"],
        issue_time=issued,
        now=now,
        model_names=["no-change"],
    )


def test_sha256_of_abc() -> None:
    assert sha256_bytes(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_publish_seals_one_hour_and_refuses_overwrite(tmp_path) -> None:
    result = _publish_no_change(tmp_path)
    rel = "data/forecasts/2026-01-02/10.jsonl"
    assert result.created is True
    assert result.rel == rel
    text = (tmp_path / rel).read_text(encoding="utf-8")
    assert text.count("\n") == 3
    rows = load_forecast_file(tmp_path, tmp_path / rel)
    assert [row.horizon for row in rows] == [1, 4, 24]
    assert [row.model for row in rows] == ["no-change"] * 3
    assert rows[0].product == "BTC-USD"
    assert rows[0].issue_time == ISSUED
    assert "issue_time" in text
    assert "model_version" in text
    assert "code_git_sha" in text
    stamp = format_utc(ISSUED)
    manifest = manifest_path(tmp_path).read_text(encoding="utf-8")
    assert manifest == f"{stamp} {rel} {result.digest}\n"

    with pytest.raises(ForecastImmutable, match="already exists"):
        run_forecasts(
            tmp_path,
            products=["BTC-USD"],
            issue_time=ISSUED,
            now=NOW,
            model_names=["no-change"],
        )
    assert manifest_path(tmp_path).read_text(encoding="utf-8") == manifest
    report = verify(tmp_path, check_git=False)
    assert report.ok, report.errors


def test_second_hour_appends_to_the_manifest(tmp_path) -> None:
    first = _publish_no_change(tmp_path)
    later = ISSUED + timedelta(hours=1)
    append_observations(tmp_path, [Observation(symbol="BTC-USD", time=later, close=101.0)])
    second = run_forecasts(
        tmp_path,
        products=["BTC-USD"],
        issue_time=later,
        now=later + timedelta(minutes=5),
        model_names=["no-change"],
    )
    manifest = manifest_path(tmp_path).read_bytes()
    first_line = f"{format_utc(ISSUED)} {first.rel} {first.digest}\n".encode()
    second_line = f"{format_utc(later)} {second.rel} {second.digest}\n".encode()
    assert manifest == first_line + second_line


def test_publish_refuses_a_clock_past_the_target(tmp_path) -> None:
    _seed(tmp_path)
    with pytest.raises(PublishedTooLate):
        run_forecasts(
            tmp_path,
            products=["BTC-USD"],
            issue_time=ISSUED,
            now=ISSUED + timedelta(hours=1),
            model_names=["no-change"],
        )
    assert list((tmp_path / "data" / "forecasts").rglob("*.jsonl")) == []


def test_issue_time_is_the_last_fully_closed_candle(tmp_path) -> None:
    _seed(tmp_path, issued=ISSUED)
    result = run_forecasts(
        tmp_path,
        products=["BTC-USD"],
        now=ISSUED + timedelta(minutes=7),
        model_names=["no-change"],
    )
    assert result.issue_time == ISSUED
    assert result.rel == "data/forecasts/2026-01-02/10.jsonl"


def test_actuals_conflict_and_replay(tmp_path) -> None:
    row = Observation(symbol="BTC-USD", time=ISSUED, close=100.0)
    assert append_observations(tmp_path, [row]) == 1
    assert append_observations(tmp_path, [row]) == 0
    with pytest.raises(ActualConflict):
        append_observations(tmp_path, [Observation(symbol="BTC-USD", time=ISSUED, close=101.0)])


def test_pinball_sides() -> None:
    assert pinball(0.0, -2.0, 0.25) == 0.5
    assert pinball(0.0, 2.0, 0.25) == 1.5
