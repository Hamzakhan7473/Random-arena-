# Random Walk Arena

Forecasts are append-only and never edited. Anyone can verify that a forecast existed before its target time from git history: the file's SHA-256 matches `data/manifest/hashes.log`, and the commit that introduced the file is strictly earlier than every target time inside it.

The arena ingests hourly prices, asks every model for the same three horizons, seals those forecasts, and only afterwards scores them. The web leaderboard reads the scored file. It does not compute forecasts.

## Flow

1. **Ingest prices.** `arena ingest` pages Coinbase Exchange `GET /products/{id}/candles` (granularity 3600, max 300 candles per request) and appends only new fully closed hourly candles to `data/actuals/{product}.parquet` for BTC-USD, ETH-USD, and SOL-USD. Coinbase timestamps are bucket starts. A candle is stored only when `timestamp + 1h <= now`, so the currently forming bar is never written. Models see that close as known at `timestamp + 1h`. The scheduled ingest runs at minute 2.
2. **Forecast.** At minute 7, `arena forecast` issues one hour of forecasts. The issue time is the current UTC hour. Each model forecasts horizons `1h`, `4h`, and `24h` with quantiles `[0.1, 0.25, 0.5, 0.75, 0.9]`.
3. **Write.** That hour is one file, `data/forecasts/YYYY-MM-DD/HH.jsonl`. `HH` is the UTC issue hour. The file is created once. A later run that would change its bytes is refused.
4. **Hash.** The SHA-256 of those exact bytes is appended to `data/manifest/hashes.log`. Existing lines are not rewritten.
5. **Commit and push before the target.** The forecast workflow commits only `data/forecasts` and `data/manifest/hashes.log`, runs `arena verify`, and pushes. The earliest target is one hour after the issue time, so the push has to finish before the next hour. A clock that is already at or past a target refuses to publish.
6. **Ingest the outcome later.** A later ingest commit appends the close at the target time. That close is not in the parent of the forecast commit, which is part of what `arena verify` checks.
7. **Score.** At minute 25, `arena score` refuses to run unless verification passes, then rewrites `data/scores/leaderboard.json`.
8. **Read.** The Next.js app in `web/` reads that leaderboard on each request. It does not import the engine.

```
ingest actuals  ->  forecast + hash  ->  commit/push before target
                                              |
                                              v
                         later actuals  ->  score  ->  leaderboard.json  ->  web
```

## What may change

| Path | Rule |
| --- | --- |
| `data/forecasts/YYYY-MM-DD/HH.jsonl` | Written once. Never edited, renamed, or deleted. |
| `data/manifest/hashes.log` | Append one hash line when an hour is sealed. Never rewrite a line. |
| `data/actuals/{product}.parquet` | Append closed hourly candles. Replaying the same timestamp is a no-op. A different OHLCV for a stored timestamp is an error. |
| `data/scores/leaderboard.json` | Derived. The scoring job overwrites it. |

Protect the default branch from force-pushes. The clock that matters is the commit that GitHub stored, not a rewritten local author date. `arena verify` uses the committer timestamp.

## Layout

```
engine/                 Python 3.11 package `arena` (uv or pip)
web/                    Next.js 14, TypeScript, Tailwind leaderboard
data/forecasts/         sealed JSONL, one file per issue hour
data/actuals/           append-only closed hourly parquet per product
data/scores/            leaderboard.json
data/manifest/          hashes.log
.github/workflows/      ci, ingest, forecast, score
```

## Forecast record

One JSON object per line. Keys and quantile levels are fixed, so the hashed bytes have a stable shape.

```json
{"model_id":"naive-last","symbol":"BTC-USD","issued_at":"2026-11-20T17:00:00Z","horizon":"1h","target_time":"2026-11-20T18:00:00Z","quantiles":{"0.1":100.0,"0.25":100.0,"0.5":100.0,"0.75":100.0,"0.9":100.0}}
```

- `issued_at` and `target_time` are exact UTC hours.
- `target_time` is `issued_at` plus the horizon.
- Quantile keys are exactly `0.1`, `0.25`, `0.5`, `0.75`, `0.9`, in that order.
- Quantile values are finite prices, strictly positive, and non-decreasing in the level.
- Every line in a file shares one `issued_at`, and that hour is the path: `data/forecasts/2026-11-20/17.jsonl`.
- Lines are ordered by symbol, then model id, then `1h`, `4h`, `24h`.

An hour file contains every model. If one model cannot fit, the hour is not published. A new model shows up in a later hour; it does not edit an old file.

## Manifest

Each line is the text-mode `shasum` record, with two spaces and no extra fields:

```
<64 lowercase hex>  data/forecasts/YYYY-MM-DD/HH.jsonl
```

From the repo root:

```sh
shasum -a 256 -c data/manifest/hashes.log
```

The line is written in the same commit as the forecast file. A hash added after the outcome was known does not verify.

## Actuals

Each product is one parquet file, `data/actuals/BTC-USD.parquet`, with columns `[timestamp_utc, open, high, low, close, volume]`. `timestamp_utc` is Coinbase's bucket start. The engine only stores rows that satisfy `timestamp_utc + 1h <= now`. Models and scoring treat the close as known at `timestamp_utc + 1h`. They may use closes at or before `issued_at` and nothing later. `arena verify` reads the parent of the forecast commit and fails if that snapshot is missing the issue-hour close or already contains a later close for the symbol.

## Models

All four run on CPU. They see only the consecutive hourly suffix ending at `issued_at`.

| Id | Forecast |
| --- | --- |
| `naive-last` | Every quantile is the last close. |
| `random-walk` | Gaussian log-return. Mean and variance scale with the horizon. Uses `scipy`. |
| `garch11` | GARCH(1,1) via `arch` on percent log returns. Horizon variance is the sum of the step variances. Needs 120 consecutive closes. |
| `torch-quantile` | A linear model trained at issue time with pinball loss on log returns that have already realized. Predicted log returns are clipped to [-2, 2], then sorted so the quantiles cannot cross. Uses CPU `torch`. |

`garch11` sets the history requirement for a full hour, so backfill at least 240 hours before enabling the schedule. 240 leaves room for a few missing candles; the fit itself uses the last 120.

## Scoring

The loss is the mean pinball loss of the log price at the five quantile levels. That is the same number as pinball loss on the log return versus the issue-time close, so the level of the asset does not dominate the board. Lower is better. Each scored forecast has equal weight; the per-horizon means are also stored.

A forecast is pending until its target time has arrived and the actual close is on disk. `arena score` checks the manifest and git history before it writes the leaderboard. The web renders `data/scores/leaderboard.json` and does not rescore.

## Verification

`arena verify` fails unless all of the following hold:

- Every file under `data/forecasts/` matches the hour path, parses, and hashes to the manifest line. Every manifest line points at a file that is present.
- The file is clean in the worktree and appears in exactly one first-parent commit.
- That commit's committer time is strictly before every `target_time` in the file.
- That same commit already contains the matching manifest line.
- The parent commit contains the symbol's close at `issued_at` and no later close for that symbol.

`--no-git` checks hashes only. Scoring does not use that flag.

A person can do the same check without the CLI: `shasum -a 256` the file, find the line in `hashes.log`, and compare `git log -1 --format=%cI` on that path with the target times. `%cI` is the committer time.

## Workflows

| Workflow | When | Commits |
| --- | --- | --- |
| `ingest.yml` | minute 2, or manual backfill | `data/actuals` only |
| `forecast.yml` | minute 7 | `data/forecasts` and `data/manifest/hashes.log`, then `arena verify`, then push |
| `score.yml` | minute 25 | `data/scores/leaderboard.json` only, and only after `arena score` verifies |
| `ci.yml` | push and pull request | nothing; pytest, `arena verify`, and the web build |

Checkouts that verify history use `fetch-depth: 0`. A depth of 1 cannot see the parent prices.

Linux installs CPU torch from `https://download.pytorch.org/whl/cpu` before the rest of the package, so pip does not pull a CUDA wheel. `uv` does the same via the `pytorch-cpu` index in `engine/pyproject.toml`. On macOS, CPU wheels come from PyPI.

## Operating it

Python 3.11. From a checkout of this repo:

```sh
cd engine
uv sync --extra dev
uv run arena --help
```

or:

```sh
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install --upgrade-strategy only-if-needed -e "./engine[dev]"
arena --help
```

Backfill, then commit those actuals, before the first forecast. The forecast commit needs a parent that already holds the issue-hour close.

```sh
arena ingest --symbol BTC-USD --backfill-hours 240
arena forecast --symbol BTC-USD
arena verify
arena score
```

`arena forecast` with no `--issued-at` uses the current UTC hour and the real clock. It will not seal an hour whose 1h target has already arrived. If that hour's file is already on disk and the regenerated bytes match, the forecast is left untouched and a missing manifest line is appended. A different byte string is an error.

The leaderboard dev server is `npm run dev` in `web/`. It reads `../data/scores/leaderboard.json`.
