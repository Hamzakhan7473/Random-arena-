import { HORIZONS } from "./config";
import type { HorizonScore, Leaderboard, ScoreCell } from "./types";

export type HorizonRow = {
  model: string;
  relative_error: number | null;
  relative_error_ci: [number | null, number | null] | null;
  dm_pvalue: number | null;
  directional_accuracy: number | null;
  coverage_80: number | null;
  n: number;
};

const HOUR: Record<string, number> = { "1h": 1, "4h": 4, "24h": 24 };

function weighted(values: Array<{ value: number | null; n: number }>): number | null {
  let total = 0;
  let weight = 0;
  for (const row of values) {
    if (row.value === null || row.n <= 0) {
      continue;
    }
    total += row.value * row.n;
    weight += row.n;
  }
  return weight === 0 ? null : total / weight;
}

function fromCells(cells: ScoreCell[], horizonLabel: string): HorizonRow[] {
  const hours = HOUR[horizonLabel];
  const grouped = new Map<string, ScoreCell[]>();
  for (const cell of cells) {
    if (cell.horizon !== hours) {
      continue;
    }
    const list = grouped.get(cell.model) ?? [];
    list.push(cell);
    grouped.set(cell.model, list);
  }
  const rows: HorizonRow[] = [];
  for (const [model, group] of grouped) {
    const n = group.reduce((sum, cell) => sum + cell.n_scored, 0);
    rows.push({
      model,
      relative_error: weighted(group.map((cell) => ({ value: cell.relative_error, n: cell.n_scored }))),
      relative_error_ci: (() => {
        const lo = weighted(
          group.map((cell) => ({
            value: cell.relative_error_ci ? cell.relative_error_ci[0] : null,
            n: cell.n_scored,
          })),
        );
        const hi = weighted(
          group.map((cell) => ({
            value: cell.relative_error_ci ? cell.relative_error_ci[1] : null,
            n: cell.n_scored,
          })),
        );
        return lo === null || hi === null ? null : [lo, hi];
      })(),
      dm_pvalue: weighted(group.map((cell) => ({ value: cell.dm_pvalue, n: cell.n_scored }))),
      directional_accuracy: weighted(
        group.map((cell) => ({ value: cell.directional_accuracy, n: cell.n_scored })),
      ),
      coverage_80: weighted(group.map((cell) => ({ value: cell.coverage_80, n: cell.n_scored }))),
      n,
    });
  }
  return rows;
}

function fromAggregates(board: Leaderboard, horizonLabel: string): HorizonRow[] {
  return board.rows
    .map((row) => {
      const cell: HorizonScore | undefined = row.by_horizon?.[horizonLabel];
      if (!cell || (cell.n_scored ?? cell.n ?? 0) === 0) {
        return null;
      }
      return {
        model: row.model,
        relative_error: cell.relative_error,
        relative_error_ci: cell.relative_error_ci ?? null,
        dm_pvalue: cell.dm_pvalue ?? null,
        directional_accuracy: cell.directional_accuracy ?? null,
        coverage_80: cell.coverage_80 ?? null,
        n: cell.n_scored ?? cell.n ?? 0,
      } satisfies HorizonRow;
    })
    .filter((row): row is HorizonRow => row !== null);
}

export function horizonTables(board: Leaderboard): Record<string, HorizonRow[]> {
  const tables: Record<string, HorizonRow[]> = {};
  for (const horizon of board.horizons.length ? board.horizons : HORIZONS) {
    const fromBoard = fromCells(board.cells ?? [], horizon);
    tables[horizon] = fromBoard.length > 0 ? fromBoard : fromAggregates(board, horizon);
    tables[horizon].sort((a, b) => {
      if (a.relative_error === null && b.relative_error === null) {
        return a.model.localeCompare(b.model);
      }
      if (a.relative_error === null) {
        return 1;
      }
      if (b.relative_error === null) {
        return -1;
      }
      if (a.relative_error !== b.relative_error) {
        return a.relative_error - b.relative_error;
      }
      return a.model.localeCompare(b.model);
    });
  }
  return tables;
}

export function modelRows(board: Leaderboard, model: string): Record<string, HorizonRow | undefined> {
  const tables = horizonTables(board);
  const found: Record<string, HorizonRow | undefined> = {};
  for (const horizon of Object.keys(tables)) {
    found[horizon] = tables[horizon].find((row) => row.model === model);
  }
  return found;
}

export function findModel(names: string[], slug: string): string | undefined {
  const needle = slug.trim().toLowerCase();
  return names.find((name) => name.toLowerCase() === needle);
}

export function allModels(board: Leaderboard): string[] {
  const names = new Set<string>();
  for (const row of board.rows ?? []) {
    names.add(row.model);
  }
  for (const cell of board.cells ?? []) {
    names.add(cell.model);
  }
  if (board.retrospective) {
    for (const name of allModels(board.retrospective)) {
      names.add(name);
    }
  }
  return [...names].sort();
}

export function barDomain(rows: HorizonRow[]): { min: number; max: number } {
  let max = 2;
  for (const row of rows) {
    for (const value of [row.relative_error, row.relative_error_ci?.[0], row.relative_error_ci?.[1]]) {
      if (typeof value === "number" && Number.isFinite(value)) {
        max = Math.max(max, value * 1.08);
      }
    }
  }
  return { min: 0, max };
}
