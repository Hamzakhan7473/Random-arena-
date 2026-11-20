export type HorizonScore = {
  relative_error: number | null;
  relative_error_ci: [number | null, number | null] | null;
  pinball?: number | null;
  coverage_80: number | null;
  directional_accuracy: number | null;
  dm_pvalue: number | null;
  n_scored: number;
  n?: number;
  beats_random_walk?: boolean;
};

export type LeaderboardRow = {
  model: string;
  model_id?: string;
  relative_error: number | null;
  pinball?: number | null;
  n_scored: number;
  beats_random_walk?: boolean;
  by_horizon: Record<string, HorizonScore>;
};

export type ScoreCell = {
  model: string;
  product: string;
  horizon: number;
  n_scored: number;
  relative_error: number | null;
  relative_error_ci: [number | null, number | null] | null;
  pinball?: number | null;
  coverage_80: number | null;
  directional_accuracy: number | null;
  dm_pvalue: number | null;
  dm_statistic?: number | null;
  spa_pvalue?: number | null;
  beats_random_walk?: boolean;
};

export type Leaderboard = {
  generated_at: string | null;
  as_of: string | null;
  live_since?: string | null;
  metric: string;
  note?: string;
  label?: string;
  kind?: string;
  live?: boolean;
  precommitted?: boolean;
  quantiles: number[];
  horizons: string[];
  n_forecasts: number;
  n_scored: number;
  n_pending: number;
  spa?: Record<string, unknown>;
  cells: ScoreCell[];
  rows: LeaderboardRow[];
  retrospective?: Leaderboard | null;
  cutoffs?: Record<string, { cutoff: string | null; source: string; note: string }>;
};

export type HistoryFile = {
  snapshots: Array<{
    date: string;
    generated_at?: string | null;
    n_scored?: number;
    n_pending?: number;
  }>;
};

export type ScoreBundle = {
  files: Record<string, unknown>;
  live: Leaderboard;
  retrospective: Leaderboard | null;
  history: HistoryFile;
  source: "github" | "local";
};

export function liveSinceLabel(board: Leaderboard): string {
  if (board.live_since) {
    return `LIVE since ${board.live_since}`;
  }
  if (board.as_of) {
    return `LIVE since ${board.as_of}`;
  }
  return "LIVE since —";
}
