"use client";

import { useState } from "react";

import { canonicalForecastLine, forecastRel, parseForecastLine, sha256Hex } from "@/lib/forecast-line";

type VerifyResponse = {
  path: string;
  line_hash: string;
  canonical_line_hash: string;
  file_hash: string | null;
  line_in_file: boolean | null;
  manifest_entry: string | null;
  manifest_matches: boolean | null;
  commit_time: string | null;
  commit_sha: string | null;
  commit_url: string | null;
  errors: string[];
};

const EXAMPLE = `{"issue_time":"2026-01-02T10:00:00Z","target_time":"2026-01-02T11:00:00Z","model":"no-change","product":"BTC-USD","horizon":1,"quantiles":{"0.1":100.0,"0.25":100.0,"0.5":100.0,"0.75":100.0,"0.9":100.0},"model_version":"1","code_git_sha":"unknown"}`;

export function VerifyForm() {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [localHash, setLocalHash] = useState<string | null>(null);
  const [canonicalHash, setCanonicalHash] = useState<string | null>(null);
  const [path, setPath] = useState<string | null>(null);
  const [result, setResult] = useState<VerifyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setResult(null);
    setPending(true);
    try {
      const parsed = parseForecastLine(text);
      const canonical = canonicalForecastLine(parsed);
      const [rawHash, canonHash] = await Promise.all([sha256Hex(text.trim()), sha256Hex(`${canonical}\n`)]);
      setLocalHash(rawHash);
      setCanonicalHash(canonHash);
      setPath(forecastRel(parsed.issue_time));
      const response = await fetch("/api/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line: text }),
      });
      const payload = (await response.json()) as VerifyResponse & { error?: string };
      if (!response.ok) {
        throw new Error(payload.error || "Verify failed.");
      }
      setResult(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not parse that line.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mt-8">
      <label htmlFor="forecast-line" className="text-sm font-medium">
        Forecast line
      </label>
      <textarea
        id="forecast-line"
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={8}
        spellCheck={false}
        placeholder={EXAMPLE}
        className="mt-2 w-full rounded-md border border-neutral-300 p-3 font-mono text-xs leading-5"
      />
      <div className="mt-3 flex flex-wrap gap-3">
        <button
          type="submit"
          disabled={pending || text.trim() === ""}
          className="rounded-md bg-neutral-950 px-4 py-2 text-sm text-white disabled:opacity-40"
        >
          {pending ? "Hashing…" : "Hash and look up"}
        </button>
        <button
          type="button"
          className="text-sm text-neutral-600 underline-offset-2 hover:underline"
          onClick={() => setText(EXAMPLE)}
        >
          Paste example
        </button>
      </div>
      {error ? <p className="mt-4 text-sm text-red-700">{error}</p> : null}
      {path ? (
        <dl className="mt-8 space-y-3 text-sm">
          <Row label="Expected file" value={path} />
          <Row label="SHA-256 of pasted text" value={localHash ?? "—"} mono />
          <Row label="SHA-256 of canonical line + newline" value={canonicalHash ?? "—"} mono />
          {result ? (
            <>
              <Row label="File SHA-256" value={result.file_hash ?? "file not found"} mono />
              <Row
                label="Line in file"
                value={result.line_in_file === null ? "—" : result.line_in_file ? "yes" : "no"}
              />
              <Row label="Manifest entry" value={result.manifest_entry ?? "no matching hashes.log line"} mono />
              <Row
                label="Manifest matches file"
                value={result.manifest_matches === null ? "—" : result.manifest_matches ? "yes" : "no"}
              />
              <Row label="Commit time" value={result.commit_time ?? "not found on GitHub"} />
              <Row
                label="Commit"
                value={result.commit_url ?? result.commit_sha ?? "—"}
                href={result.commit_url ?? undefined}
              />
              {result.errors.length > 0 ? (
                <div>
                  <dt className="text-neutral-500">Notes</dt>
                  <dd className="mt-1">
                    {result.errors.map((item) => (
                      <p key={item}>{item}</p>
                    ))}
                  </dd>
                </div>
              ) : null}
            </>
          ) : null}
        </dl>
      ) : null}
    </form>
  );
}

function Row({
  label,
  value,
  mono,
  href,
}: {
  label: string;
  value: string;
  mono?: boolean;
  href?: string;
}) {
  return (
    <div>
      <dt className="text-neutral-500">{label}</dt>
      <dd className={`mt-1 break-all ${mono ? "font-mono text-xs" : ""}`}>
        {href ? (
          <a href={href} className="underline-offset-2 hover:underline">
            {value}
          </a>
        ) : (
          value
        )}
      </dd>
    </div>
  );
}
