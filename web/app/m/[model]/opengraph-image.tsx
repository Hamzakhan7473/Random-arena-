import { ImageResponse } from "@vercel/og";

import { KNOWN_MODELS, REVALIDATE_SECONDS, SITE_HOST } from "@/lib/config";
import { formatCI, formatNumber } from "@/lib/format";
import { allModels, findModel, modelRows } from "@/lib/horizon";
import { loadScoreBundle } from "@/lib/scores";

export const runtime = "nodejs";
export const revalidate = REVALIDATE_SECONDS;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Random Walk Arena model card";

export default async function OpenGraphImage({ params }: { params: { model: string } }) {
  const scores = await loadScoreBundle();
  const names = [...new Set([...KNOWN_MODELS, ...allModels(scores.live)])];
  const model = findModel(names, params.model) ?? decodeURIComponent(params.model);
  const hours = modelRows(scores.live, model);
  const row = hours["24h"] ?? hours["4h"] ?? hours["1h"];
  const value = row?.relative_error ?? null;
  const ci = row?.relative_error_ci ?? null;
  const lo = typeof ci?.[0] === "number" ? ci[0] : value ?? 0;
  const hi = typeof ci?.[1] === "number" ? ci[1] : value ?? 2;
  const domainMax = Math.max(2, hi * 1.08, (value ?? 1) * 1.08);
  const x = (point: number) => Math.min(Math.max(point, 0), domainMax) / domainMax * 1040;
  const rw = x(1);
  const marker = value === null ? rw : x(value);
  const whiskerLo = x(Math.min(lo, hi));
  const whiskerWidth = Math.max(8, x(Math.max(lo, hi)) - whiskerLo);

  return new ImageResponse(
    (
      <div
        style={{
          width: "1200px",
          height: "630px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          backgroundColor: "#ffffff",
          color: "#111111",
          padding: "64px 72px",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", fontSize: 26, color: "#666666" }}>Random Walk Arena</div>
          <div style={{ display: "flex", fontSize: 64, fontWeight: 700, marginTop: 8 }}>{model}</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", fontSize: 24, color: "#666666" }}>Relative error vs random walk</div>
          <div style={{ display: "flex", alignItems: "flex-end", marginTop: 6 }}>
            <div style={{ display: "flex", fontSize: 80, fontWeight: 700, lineHeight: 1 }}>{formatNumber(value)}</div>
            <div style={{ display: "flex", fontSize: 24, color: "#666666", marginLeft: 16, marginBottom: 10 }}>
              95% CI {formatCI(ci)}
            </div>
          </div>
          <div
            style={{
              display: "flex",
              width: "1040px",
              height: "56px",
              marginTop: 28,
              backgroundColor: "#f3f3f3",
              position: "relative",
            }}
          >
            <div
              style={{
                display: "flex",
                position: "absolute",
                left: whiskerLo,
                top: 24,
                width: whiskerWidth,
                height: 8,
                backgroundColor: "#9a9a9a",
              }}
            />
            <div
              style={{
                display: "flex",
                position: "absolute",
                left: rw,
                top: 0,
                width: 3,
                height: 56,
                backgroundColor: "#111111",
              }}
            />
            <div
              style={{
                display: "flex",
                position: "absolute",
                left: marker - 11,
                top: 17,
                width: 22,
                height: 22,
                borderRadius: 22,
                backgroundColor: "#111111",
              }}
            />
          </div>
          <div style={{ display: "flex", fontSize: 20, color: "#666666", marginTop: 10 }}>
            random walk = 1.0
          </div>
        </div>
        <div style={{ display: "flex", fontSize: 28, color: "#555555" }}>{SITE_HOST}</div>
      </div>
    ),
    { width: 1200, height: 630 },
  );
}
