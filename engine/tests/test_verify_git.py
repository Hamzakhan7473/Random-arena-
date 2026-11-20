import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from arena.actuals import append_observations
from arena.run_forecasts import run_forecasts
from arena.schema import Observation
from arena.verify import verify

ISSUED = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


def _git(root, *args: str, when: str | None = None) -> None:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Arena Test",
            "GIT_AUTHOR_EMAIL": "arena@example.com",
            "GIT_COMMITTER_NAME": "Arena Test",
            "GIT_COMMITTER_EMAIL": "arena@example.com",
        }
    )
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", *args], cwd=root, check=True, env=env, capture_output=True, text=True)


def _commit(root, message: str, when: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message, when=when)


def _init(root) -> None:
    _git(root, "init", "-b", "main")


def _price(root, *moments: datetime) -> None:
    append_observations(
        root,
        [Observation(symbol="BTC-USD", time=moment, close=100.0 + index) for index, moment in enumerate(moments)],
    )


def _history(root) -> None:
    moments = [ISSUED - timedelta(hours=offset) for offset in range(47, -1, -1)]
    _price(root, *moments)


def _forecast(root, when: datetime) -> None:
    run_forecasts(
        root,
        products=["BTC-USD"],
        issue_time=ISSUED,
        now=when,
        model_names=["no-change"],
    )


def test_verify_accepts_a_commit_before_the_target(tmp_path) -> None:
    _init(tmp_path)
    _history(tmp_path)
    _commit(tmp_path, "actuals", "2026-03-01T11:50:00+00:00")
    _forecast(tmp_path, ISSUED + timedelta(minutes=6))
    _commit(tmp_path, "forecast", "2026-03-01T12:06:00+00:00")
    report = verify(tmp_path)
    assert report.ok, report.errors
    assert report.files_checked == 1


def test_verify_rejects_a_commit_that_is_not_before_the_target(tmp_path) -> None:
    _init(tmp_path)
    _history(tmp_path)
    _commit(tmp_path, "actuals", "2026-03-01T11:50:00+00:00")
    _forecast(tmp_path, ISSUED + timedelta(minutes=6))
    _commit(tmp_path, "forecast", "2026-03-01T13:00:00+00:00")
    report = verify(tmp_path)
    assert any("not before" in error for error in report.errors)


def test_verify_rejects_a_later_edit(tmp_path) -> None:
    _init(tmp_path)
    _history(tmp_path)
    _commit(tmp_path, "actuals", "2026-03-01T11:50:00+00:00")
    _forecast(tmp_path, ISSUED + timedelta(minutes=6))
    _commit(tmp_path, "forecast", "2026-03-01T12:06:00+00:00")
    path = tmp_path / "data" / "forecasts" / "2026-03-01" / "12.jsonl"
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("{", "{ ", 1), encoding="utf-8")
    assert path.read_text(encoding="utf-8") != original
    _commit(tmp_path, "rewrite", "2026-03-01T12:07:00+00:00")
    report = verify(tmp_path)
    assert any("exactly one commit" in error for error in report.errors)


def test_verify_rejects_prices_from_after_the_issue_hour(tmp_path) -> None:
    _init(tmp_path)
    _history(tmp_path)
    _price(tmp_path, ISSUED + timedelta(hours=1))
    _commit(tmp_path, "actuals", "2026-03-01T11:50:00+00:00")
    _forecast(tmp_path, ISSUED + timedelta(minutes=6))
    _commit(tmp_path, "forecast", "2026-03-01T12:06:00+00:00")
    report = verify(tmp_path)
    assert any("after the issue hour" in error for error in report.errors)


def test_verify_rejects_a_root_commit(tmp_path) -> None:
    _init(tmp_path)
    _history(tmp_path)
    _forecast(tmp_path, ISSUED + timedelta(minutes=6))
    _commit(tmp_path, "everything", "2026-03-01T12:06:00+00:00")
    report = verify(tmp_path)
    assert any("no parent" in error for error in report.errors)


def test_scripts_verify_uses_git_log_before_target(tmp_path) -> None:
    from pathlib import Path

    _init(tmp_path)
    _history(tmp_path)
    _commit(tmp_path, "actuals", "2026-03-01T11:50:00+00:00")
    _forecast(tmp_path, ISSUED + timedelta(minutes=6))
    _commit(tmp_path, "forecast", "2026-03-01T12:06:00+00:00")
    script = Path(__file__).resolve().parents[2] / "scripts" / "verify.py"
    rel = "data/forecasts/2026-03-01/12.jsonl"
    ok = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), rel],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "earlier than every target_time" in ok.stdout
    assert "manifest sha256" in ok.stdout
