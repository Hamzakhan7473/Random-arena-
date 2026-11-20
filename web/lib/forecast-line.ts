export type ForecastLine = {
  issue_time: string;
  target_time: string;
  model: string;
  product: string;
  horizon: number;
  quantiles: Record<string, number>;
  model_version: string;
  code_git_sha: string;
};

const QUANTILE_KEYS = ["0.1", "0.25", "0.5", "0.75", "0.9"] as const;

export function forecastRel(issueTime: string): string {
  const stamp = issueTime.endsWith("Z") ? issueTime : `${issueTime}Z`;
  const day = stamp.slice(0, 10);
  const hour = stamp.slice(11, 13);
  return `data/forecasts/${day}/${hour}.jsonl`;
}

export function canonicalForecastLine(row: ForecastLine): string {
  const quantiles: Record<string, number> = {};
  for (const key of QUANTILE_KEYS) {
    quantiles[key] = row.quantiles[key];
  }
  return JSON.stringify({
    issue_time: row.issue_time,
    target_time: row.target_time,
    model: row.model,
    product: row.product,
    horizon: row.horizon,
    quantiles,
    model_version: row.model_version,
    code_git_sha: row.code_git_sha,
  });
}

export function parseForecastLine(text: string): ForecastLine {
  const raw = JSON.parse(text.trim()) as Record<string, unknown>;
  const issue = String(raw.issue_time ?? raw.issued_at ?? "");
  const model = String(raw.model ?? raw.model_id ?? "");
  const product = String(raw.product ?? raw.symbol ?? "");
  const horizonRaw = raw.horizon;
  const horizon =
    typeof horizonRaw === "string" && horizonRaw.endsWith("h")
      ? Number(horizonRaw.slice(0, -1))
      : Number(horizonRaw);
  const quantiles = (raw.quantiles ?? {}) as Record<string, number>;
  if (!issue || !model || !product || !Number.isFinite(horizon)) {
    throw new Error("Need issue_time, model, product, and horizon.");
  }
  for (const key of QUANTILE_KEYS) {
    if (typeof quantiles[key] !== "number") {
      throw new Error(`Missing quantile ${key}.`);
    }
  }
  return {
    issue_time: issue,
    target_time: String(raw.target_time ?? ""),
    model,
    product,
    horizon,
    quantiles,
    model_version: String(raw.model_version ?? "1"),
    code_git_sha: String(raw.code_git_sha ?? "unknown"),
  };
}

export async function sha256Hex(payload: string): Promise<string> {
  const encoded = new TextEncoder().encode(payload);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
