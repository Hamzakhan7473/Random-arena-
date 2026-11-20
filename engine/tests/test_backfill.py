from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import pytest

from arena.actuals import append_observations, load_observations
from arena.backfill import (
    CUTOFFS,
    DEFAULT_ORIGIN,
    STEP_HOURS,
    first_allowed_issue,
    issue_schedule,
    latest_cutoff,
    load_retrospective_forecasts,
    refuse_before_cutoffs,
    run_backfill,
    score_retrospective,
)
from arena.errors import BackfillCutoff
from arena.paths import leaderboard_path, manifest_path, retrospective_leaderboard_path
from arena.run_forecasts import run_forecasts
from arena.schema import Observation
from arena.score import build_leaderboard, score_repo, write_scores
from arena.store import load_all_forecasts

ORIGIN = datetime(2025, 11, 1, tzinfo=timezone.utc)
NOW = datetime(2025, 11, 5, 12, tzinfo=timezone.utc)


def _seed(root: Path, start: datetime, end: datetime, symbol: str = "BTC-USD", close: float = 100.0) -> None:
    rows = []
    stamp = start
    index = 0
    while stamp <= end:
        rows.append(Observation(symbol=symbol, time=stamp, close=close * (1.001**index)))
        stamp += timedelta(hours=1)
        index += 1
    append_observations(root, rows)


def test_cutoffs_have_source_links_and_latest_is_after_stated_dates() -> None:
    for name, row in CUTOFFS.items():
        assert "source" in row, name
        assert "note" in row, name
        if row["cutoff"] is not None:
            assert str(row["source"]).startswith("https://"), name
    bound = latest_cutoff()
    assert bound == datetime.fromisoformat("2025-10-30").date()
    assert first_allowed_issue() == datetime(2025, 10, 31, tzinfo=timezone.utc)
    assert DEFAULT_ORIGIN >= first_allowed_issue()
    assert DEFAULT_ORIGIN == ORIGIN
    assert STEP_HOURS == 24
    refuse_before_cutoffs(DEFAULT_ORIGIN)


def test_refuses_to_backfill_before_the_latest_cutoff(tmp_path: Path) -> None:
    too_early = first_allowed_issue() - timedelta(hours=24)
    with pytest.raises(BackfillCutoff, match="refusing to backfill before the latest training cutoff"):
        refuse_before_cutoffs(too_early)
    with pytest.raises(BackfillCutoff, match="https://huggingface.co/amazon/chronos-2"):
        issue_schedule(too_early, too_early + timedelta(days=2))
    with pytest.raises(BackfillCutoff, match="refusing to backfill before the latest training cutoff"):
        run_backfill(
            tmp_path,
            products=["BTC-USD"],
            model_names=["no-change"],
            start=too_early,
            end=too_early,
            now=NOW,
        )


def test_walk_forward_is_daily_under_retrospective_and_not_live(tmp_path: Path) -> None:
    first = ORIGIN
    last = ORIGIN + timedelta(days=2)
    _seed(tmp_path, first - timedelta(hours=48), last + timedelta(hours=24))
    result = run_backfill(
        tmp_path,
        products=["BTC-USD"],
        model_names=["no-change"],
        start=first,
        end=last,
        now=NOW,
    )
    expected = [first, first + timedelta(days=1), first + timedelta(days=2)]
    assert result.issued == expected
    rels = [
        "data/retrospective/forecasts/2025-11-01/00.jsonl",
        "data/retrospective/forecasts/2025-11-02/00.jsonl",
        "data/retrospective/forecasts/2025-11-03/00.jsonl",
    ]
    for rel in rels:
        assert (tmp_path / rel).is_file()
    assert list((tmp_path / "data" / "forecasts").rglob("*.jsonl")) == []
    assert not manifest_path(tmp_path).exists()
    rows = load_retrospective_forecasts(tmp_path)
    assert {row.issue_time for row in rows} == set(expected)
    assert all(row.model == "no-change" for row in rows)
    assert load_all_forecasts(tmp_path) == []

    board = json.loads(leaderboard_path(tmp_path).read_text(encoding="utf-8"))
    retro = board["retrospective"]
    assert retro["label"] == "RETROSPECTIVE"
    assert retro["live"] is False
    assert retro["precommitted"] is False
    assert "not live" in retro["note"].lower()
    assert "not precommitted" in retro["note"].lower()
    assert retro["n_forecasts"] == result.n_forecasts
    assert board.get("label") != "RETROSPECTIVE"
    standalone = json.loads(retrospective_leaderboard_path(tmp_path).read_text(encoding="utf-8"))
    assert standalone["label"] == "RETROSPECTIVE"
    assert standalone["cutoffs"]["chronos-2"]["source"] == "https://huggingface.co/amazon/chronos-2"
    assert standalone["cutoffs"]["timesfm-2.5"]["source"] == "https://huggingface.co/google/timesfm-2.5-200m-pytorch"
    assert standalone["cutoffs"]["chronos-bolt-small"]["source"] == "https://huggingface.co/amazon/chronos-bolt-small"


def test_scores_the_same_way_as_live_join(tmp_path: Path) -> None:
    first = ORIGIN
    last = ORIGIN + timedelta(days=1)
    _seed(tmp_path, first - timedelta(hours=48), last + timedelta(hours=24))
    run_backfill(
        tmp_path,
        products=["BTC-USD"],
        model_names=["no-change"],
        start=first,
        end=last,
        now=NOW,
    )
    forecasts = load_retrospective_forecasts(tmp_path)
    actuals = {(row.symbol, row.time): row.close for row in load_observations(tmp_path)}
    expected = build_leaderboard(forecasts, actuals, NOW)
    scored = score_retrospective(tmp_path, now=NOW)
    assert scored["label"] == "RETROSPECTIVE"
    assert scored["cells"] == expected["cells"]
    assert scored["n_scored"] == expected["n_scored"]
    clone = [cell for cell in scored["cells"] if cell["model"] == "no-change"]
    assert clone
    assert all(cell["relative_error"] == 1.0 for cell in clone)


def test_live_score_preserves_retrospective_and_ignores_those_files(tmp_path: Path) -> None:
    first = ORIGIN
    _seed(tmp_path, first - timedelta(hours=48), first + timedelta(hours=24))
    run_backfill(
        tmp_path,
        products=["BTC-USD"],
        model_names=["no-change"],
        start=first,
        end=first,
        now=NOW,
    )
    live = run_forecasts(
        tmp_path,
        products=["BTC-USD"],
        model_names=["no-change"],
        issue_time=first,
        now=first + timedelta(minutes=5),
    )
    assert live.rel.startswith("data/forecasts/")
    board = score_repo(tmp_path, now=NOW)
    write_scores(tmp_path, board)
    payload = json.loads(leaderboard_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["retrospective"]["label"] == "RETROSPECTIVE"
    assert payload["n_forecasts"] == 3
    assert payload["retrospective"]["n_forecasts"] == 3
    assert payload.get("label") != "RETROSPECTIVE"


def test_default_window_is_november_first_through_yesterday(tmp_path: Path) -> None:
    now = datetime(2025, 11, 4, 15, tzinfo=timezone.utc)
    _seed(tmp_path, ORIGIN - timedelta(hours=48), now)
    result = run_backfill(tmp_path, products=["BTC-USD"], model_names=["no-change"], now=now)
    assert result.start == ORIGIN
    assert result.end == datetime(2025, 11, 3, tzinfo=timezone.utc)
    assert result.issued == [
        ORIGIN,
        ORIGIN + timedelta(days=1),
        ORIGIN + timedelta(days=2),
    ]
