import { readFileSync, readdirSync, existsSync } from "fs";
import path from "path";

import { GITHUB_BRANCH, GITHUB_REPO, HORIZONS, REVALIDATE_SECONDS, githubRawUrl } from "./config";
import type { HistoryFile, Leaderboard, ScoreBundle } from "./types";

export type { HistoryFile, Leaderboard, ScoreBundle } from "./types";

const FETCH_INIT: RequestInit = { next: { revalidate: REVALIDATE_SECONDS } };

function emptyBoard(): Leaderboard {
  return {
    generated_at: null,
    as_of: null,
    live_since: null,
    metric: "mase_median_vs_nochange",
    quantiles: [0.1, 0.25, 0.5, 0.75, 0.9],
    horizons: [...HORIZONS],
    n_forecasts: 0,
    n_scored: 0,
    n_pending: 0,
    cells: [],
    rows: [],
  };
}

function localScoresDir(): string {
  return path.resolve(process.cwd(), "..", "data", "scores");
}

function coerceBoard(value: unknown): Leaderboard {
  const parsed = (value && typeof value === "object" ? value : {}) as Partial<Leaderboard>;
  const board: Leaderboard = {
    ...emptyBoard(),
    ...parsed,
    cells: Array.isArray(parsed.cells) ? parsed.cells : [],
    rows: Array.isArray(parsed.rows) ? parsed.rows : [],
    horizons: Array.isArray(parsed.horizons) && parsed.horizons.length ? parsed.horizons : [...HORIZONS],
    quantiles: Array.isArray(parsed.quantiles) ? parsed.quantiles : [0.1, 0.25, 0.5, 0.75, 0.9],
    n_forecasts: Number(parsed.n_forecasts ?? 0),
    n_scored: Number(parsed.n_scored ?? 0),
    n_pending: Number(parsed.n_pending ?? 0),
    metric: parsed.metric ?? "mase_median_vs_nochange",
  };
  if (parsed.retrospective && typeof parsed.retrospective === "object") {
    const retro = coerceBoard(parsed.retrospective);
    retro.label = "RETROSPECTIVE";
    retro.live = false;
    retro.precommitted = false;
    board.retrospective = retro;
  }
  return board;
}

async function fetchGithubJson(rel: string): Promise<unknown | null> {
  const url = githubRawUrl(rel);
  if (!url) {
    return null;
  }
  try {
    const response = await fetch(url, FETCH_INIT);
    if (!response.ok) {
      return null;
    }
    return await response.json();
  } catch {
    return null;
  }
}

async function listGithubScoreFiles(): Promise<string[] | null> {
  if (!GITHUB_REPO) {
    return null;
  }
  const url = `https://api.github.com/repos/${GITHUB_REPO}/contents/data/scores?ref=${encodeURIComponent(GITHUB_BRANCH)}`;
  try {
    const response = await fetch(url, {
      ...FETCH_INIT,
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!response.ok) {
      return null;
    }
    const listing = (await response.json()) as Array<{ name?: string; type?: string }>;
    if (!Array.isArray(listing)) {
      return null;
    }
    return listing
      .filter((entry) => entry.type === "file" && typeof entry.name === "string" && entry.name.endsWith(".json"))
      .map((entry) => entry.name as string);
  } catch {
    return null;
  }
}

function listLocalScoreFiles(): string[] {
  const directory = localScoresDir();
  if (!existsSync(directory)) {
    return [];
  }
  return readdirSync(directory).filter((name) => name.endsWith(".json"));
}

function readLocalJson(name: string): unknown | null {
  const file = path.join(localScoresDir(), name);
  if (!existsSync(file)) {
    return null;
  }
  return JSON.parse(readFileSync(file, "utf8"));
}

export async function loadScoreBundle(): Promise<ScoreBundle> {
  const githubNames = await listGithubScoreFiles();
  const names = githubNames && githubNames.length > 0 ? githubNames : listLocalScoreFiles();
  const source: "github" | "local" = githubNames && githubNames.length > 0 ? "github" : "local";
  const files: Record<string, unknown> = {};
  await Promise.all(
    names.map(async (name) => {
      const rel = `data/scores/${name}`;
      const loaded = source === "github" ? await fetchGithubJson(rel) : readLocalJson(name);
      if (loaded != null) {
        files[name] = loaded;
      }
    }),
  );
  if (!files["leaderboard.json"]) {
    const local = readLocalJson("leaderboard.json");
    if (local) {
      files["leaderboard.json"] = local;
    }
  }
  if (!files["history.json"]) {
    const local = readLocalJson("history.json");
    if (local) {
      files["history.json"] = local;
    }
  }
  const live = coerceBoard(files["leaderboard.json"] ?? emptyBoard());
  const historyRaw = files["history.json"] as HistoryFile | undefined;
  const history: HistoryFile = {
    snapshots: Array.isArray(historyRaw?.snapshots) ? historyRaw.snapshots : [],
  };
  if (!live.live_since && history.snapshots.length > 0) {
    const dated = history.snapshots.map((row) => row.date).filter(Boolean).sort();
    live.live_since = dated[0] ?? null;
  }
  return {
    files,
    live,
    retrospective: live.retrospective ?? null,
    history,
    source,
  };
}
