import { formatCI, formatNumber } from "@/lib/format";

export function CIBar({
  value,
  ci,
  min,
  max,
}: {
  value: number | null;
  ci: [number | null, number | null] | null;
  min: number;
  max: number;
}) {
  const span = Math.max(max - min, 0.0001);
  const pct = (point: number) => `${((point - min) / span) * 100}%`;
  const lo = ci?.[0] ?? value;
  const hi = ci?.[1] ?? value;
  const line = pct(1);
  const hasValue = value !== null && Number.isFinite(value);

  return (
    <div className="min-w-[11rem]">
      <div className="relative h-7 w-full rounded-sm bg-neutral-100">
        <div
          className="absolute inset-y-0 w-px bg-neutral-950"
          style={{ left: line }}
          aria-hidden="true"
        />
        {lo !== null && hi !== null ? (
          <div
            className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-neutral-400"
            style={{
              left: pct(Math.min(lo, hi)),
              width: `max(2px, calc(${pct(Math.max(lo, hi))} - ${pct(Math.min(lo, hi))}))`,
            }}
          />
        ) : null}
        {hasValue ? (
          <div
            className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-neutral-950"
            style={{ left: pct(value) }}
          />
        ) : null}
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-neutral-500">
        <span>{hasValue ? formatNumber(value) : "—"}</span>
        <span>{formatCI(ci)}</span>
      </div>
    </div>
  );
}

export function RandomWalkAxis({ min, max }: { min: number; max: number }) {
  const span = Math.max(max - min, 0.0001);
  const left = ((1 - min) / span) * 100;
  return (
    <div className="relative h-5 text-[11px] uppercase tracking-wide text-neutral-500">
      <span className="absolute -translate-x-1/2" style={{ left: `${left}%` }}>
        random walk
      </span>
    </div>
  );
}
