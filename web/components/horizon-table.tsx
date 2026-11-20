import Link from "next/link";

import { CIBar, RandomWalkAxis } from "@/components/ci-bar";
import { formatPValue, formatPercent, slugifyModel } from "@/lib/format";
import { barDomain, type HorizonRow } from "@/lib/horizon";

export function HorizonTable({ horizon, rows }: { horizon: string; rows: HorizonRow[] }) {
  const domain = barDomain(rows);

  return (
    <section className="mt-10">
      <h2 className="text-lg font-semibold tracking-tight">{horizon}</h2>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-neutral-500">No scored forecasts for this horizon yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
            <caption className="sr-only">
              {horizon} relative error versus the random walk, with a 95% CI, DM p-value, directional
              accuracy, 80% coverage, and sample size. Lower relative error is better. No winner is
              declared.
            </caption>
            <thead>
              <tr className="border-b border-neutral-200 text-[11px] uppercase tracking-wide text-neutral-500">
                <th className="py-2 pr-3 font-medium">Model</th>
                <th className="py-2 pr-3 font-medium">
                  Relative error vs random walk
                  <RandomWalkAxis min={domain.min} max={domain.max} />
                </th>
                <th className="py-2 pr-3 text-right font-medium">DM p</th>
                <th className="py-2 pr-3 text-right font-medium">Dir. acc.</th>
                <th className="py-2 pr-3 text-right font-medium">Coverage</th>
                <th className="py-2 text-right font-medium">n</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.model} className="border-b border-neutral-100">
                  <td className="py-3 pr-3 font-medium">
                    <Link href={`/m/${slugifyModel(row.model)}`} className="underline-offset-2 hover:underline">
                      {row.model}
                    </Link>
                  </td>
                  <td className="py-3 pr-3">
                    <CIBar value={row.relative_error} ci={row.relative_error_ci} min={domain.min} max={domain.max} />
                  </td>
                  <td className="py-3 pr-3 text-right tabular-nums">{formatPValue(row.dm_pvalue)}</td>
                  <td className="py-3 pr-3 text-right tabular-nums">{formatPercent(row.directional_accuracy)}</td>
                  <td className="py-3 pr-3 text-right tabular-nums">{formatPercent(row.coverage_80)}</td>
                  <td className="py-3 text-right tabular-nums text-neutral-500">{row.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
