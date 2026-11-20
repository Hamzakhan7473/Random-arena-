from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from arena.errors import ArenaError
from arena.paths import find_root
from arena.timeutil import as_utc_hour, parse_utc


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=None, help="repository root")
    parser = argparse.ArgumentParser(prog="arena", parents=[common])
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", parents=[common], help="append closed hourly candles from Coinbase")
    ingest.add_argument("--symbol", action="append", default=None)
    ingest.add_argument("--backfill-hours", type=int, default=None)

    forecast = commands.add_parser("forecast", parents=[common], help="write and seal one issue hour")
    forecast.add_argument("--symbol", action="append", default=None)
    forecast.add_argument("--product", action="append", default=None)
    forecast.add_argument("--issued-at", default=None, help="UTC hour, default the last fully closed candle")
    forecast.add_argument("--issue-time", default=None, help="alias of --issued-at")
    forecast.add_argument("--model", action="append", default=None, help="model id; default is every forecaster")

    commands.add_parser("score", parents=[common], help="rewrite data/scores/leaderboard.json from sealed forecasts")

    backfill = commands.add_parser(
        "backfill",
        parents=[common],
        help="walk-forward retrospective forecasts after published training cutoffs",
    )
    backfill.add_argument("--symbol", action="append", default=None)
    backfill.add_argument("--product", action="append", default=None)
    backfill.add_argument("--model", action="append", default=None)
    backfill.add_argument("--start", default=None, help="first UTC issue hour, default 2025-11-01T00:00:00Z")
    backfill.add_argument("--end", default=None, help="last UTC issue hour, default yesterday 00:00 UTC")

    verify = commands.add_parser("verify", parents=[common], help="check the manifest and git history")
    verify.add_argument("--no-git", action="store_true", help="check hashes only")
    return parser


def _extract_root(argv: list[str]) -> tuple[Path | None, list[str]]:
    """Accept --root before or after the subcommand."""
    root: Path | None = None
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--root":
            if index + 1 >= len(argv):
                raise ValueError("--root requires a path")
            root = Path(argv[index + 1])
            index += 2
            continue
        if token.startswith("--root="):
            root = Path(token.split("=", 1)[1])
            index += 1
            continue
        cleaned.append(token)
        index += 1
    return root, cleaned


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        root_override, raw = _extract_root(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser = _parser()
    args = parser.parse_args(raw)
    if root_override is not None:
        args.root = root_override
    try:
        root = args.root.resolve() if args.root is not None else find_root()
        if args.command == "ingest":
            _ingest(root, args.symbol, args.backfill_hours)
        elif args.command == "forecast":
            products = args.product or args.symbol
            issue = args.issue_time or args.issued_at
            _forecast(root, products, issue, args.model)
        elif args.command == "score":
            return _score(root)
        elif args.command == "backfill":
            products = args.product or args.symbol
            return _backfill(root, products, args.start, args.end, args.model)
        elif args.command == "verify":
            report = _verify(root, check_git=not args.no_git)
            return 0 if report.ok else 1
        else:
            parser.error(f"unknown command {args.command}")
    except (ArenaError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _symbols(values: list[str] | None) -> list[str]:
    if values:
        return values
    from arena.ingest import DEFAULT_PRODUCTS

    return list(DEFAULT_PRODUCTS)


def _ingest(root: Path, symbols: list[str] | None, backfill_hours: int | None) -> None:
    from arena.ingest import DEFAULT_LOOKBACK_HOURS, update_store

    lookback = DEFAULT_LOOKBACK_HOURS if backfill_hours is None else backfill_hours
    written = update_store(_symbols(symbols), root=root, lookback_hours=lookback)
    for product_id, count in written.items():
        print(f"ingested {count} new {product_id} candles")


def _forecast(root: Path, symbols: list[str] | None, issued_at: str | None, models: list[str] | None) -> None:
    from arena.run_forecasts import run_forecasts
    from arena.timeutil import format_utc

    now = datetime.now(timezone.utc)
    result = run_forecasts(
        root,
        products=_symbols(symbols),
        model_names=models,
        issue_time=as_utc_hour(parse_utc(issued_at)) if issued_at else None,
        now=now,
    )
    stamp = format_utc(result.issue_time)
    print(f"ISSUE_TIME={stamp}")
    print(f"sealed {result.rel}")
    print(result.digest)
    print(f"forecasts {stamp}")


def _score(root: Path) -> int:
    from arena.score import score_repo, write_scores

    report = _verify(root, check_git=True)
    if not report.ok:
        print("error: refusing to score forecasts that fail verification", file=sys.stderr)
        return 1
    board = score_repo(root)
    leaderboard, history = write_scores(root, board)
    print(
        f"wrote {leaderboard.relative_to(root).as_posix()} "
        f"and {history.relative_to(root).as_posix()} "
        f"({board['n_scored']} scored, {board['n_pending']} pending)"
    )
    return 0


def _backfill(
    root: Path,
    symbols: list[str] | None,
    start: str | None,
    end: str | None,
    models: list[str] | None,
) -> int:
    from arena.backfill import STEP_HOURS, latest_cutoff, run_backfill
    from arena.paths import leaderboard_path
    from arena.timeutil import format_utc

    result = run_backfill(
        root,
        products=_symbols(symbols),
        model_names=models,
        start=as_utc_hour(parse_utc(start)) if start else None,
        end=as_utc_hour(parse_utc(end)) if end else None,
    )
    print(
        f"RETROSPECTIVE {format_utc(result.start)} .. {format_utc(result.end)} "
        f"step={STEP_HOURS}h latest_cutoff={latest_cutoff().isoformat()}"
    )
    print(f"issued {len(result.issued)} hours ({result.n_forecasts} forecasts)")
    print(f"skipped {len(result.skipped)}")
    print(f"wrote {result.retrospective_path.relative_to(root).as_posix()}")
    print(f"updated {leaderboard_path(root).relative_to(root).as_posix()} retrospective")
    return 0


def _verify(root: Path, *, check_git: bool):
    from arena.verify import verify

    report = verify(root, check_git=check_git)
    if report.ok:
        print(f"ok ({report.files_checked} forecast files)")
        return report
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
