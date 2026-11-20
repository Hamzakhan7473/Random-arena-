"use client";

import { useState } from "react";

import { HorizonTable } from "@/components/horizon-table";
import { horizonTables } from "@/lib/horizon";
import { liveSinceLabel, type Leaderboard } from "@/lib/types";

export function HomeBoard({
  live,
  retrospective,
}: {
  live: Leaderboard;
  retrospective: Leaderboard | null;
}) {
  const [tab, setTab] = useState<"live" | "retrospective">("live");
  const emptyRetro: Leaderboard = {
    ...live,
    label: "RETROSPECTIVE",
    live: false,
    precommitted: false,
    rows: [],
    cells: [],
    n_forecasts: 0,
    n_scored: 0,
    n_pending: 0,
  };
  const board = tab === "retrospective" ? retrospective ?? emptyRetro : live;
  const tables = horizonTables(board);
  const labeled = tab === "retrospective";

  return (
    <div>
      <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm font-medium tracking-wide text-neutral-700">{liveSinceLabel(live)}</p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setTab("live")}
            className={`rounded-full border px-3 py-1.5 text-sm ${
              tab === "live" ? "border-neutral-950 bg-neutral-950 text-white" : "border-neutral-300"
            }`}
          >
            Live
          </button>
          <button
            type="button"
            onClick={() => setTab("retrospective")}
            className={`rounded-full border px-3 py-1.5 text-sm ${
              tab === "retrospective" ? "border-neutral-950 bg-neutral-950 text-white" : "border-neutral-300"
            }`}
          >
            Retrospective (labeled)
          </button>
        </div>
      </div>
      {labeled ? (
        <p className="mt-4 max-w-2xl text-sm text-neutral-600">
          RETROSPECTIVE. Walk-forward after published training cutoffs. Not live. Not precommitted.
        </p>
      ) : (
        <p className="mt-4 max-w-2xl text-sm text-neutral-600">
          Sealed hourly forecasts, scored only after the target close is on disk. Relative error is
          MASE versus NoChange. A value of 1.0 is the random walk. No winner is declared.
        </p>
      )}
      {(board.horizons.length ? board.horizons : ["1h", "4h", "24h"]).map((horizon) => (
        <HorizonTable key={horizon} horizon={horizon} rows={tables[horizon] ?? []} />
      ))}
    </div>
  );
}
