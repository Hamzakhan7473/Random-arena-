import { createHash } from "crypto";
import { existsSync, readFileSync } from "fs";
import path from "path";
import { NextResponse } from "next/server";

import { GITHUB_BRANCH, GITHUB_REPO, githubRawUrl } from "@/lib/config";
import { canonicalForecastLine, forecastRel, parseForecastLine } from "@/lib/forecast-line";

export const runtime = "nodejs";

type Body = { line?: string };

function digest(payload: string | Buffer): string {
  return createHash("sha256").update(payload).digest("hex");
}

function repoRoot(): string {
  return path.resolve(process.cwd(), "..");
}

function readLocal(rel: string): string | null {
  const file = path.join(repoRoot(), rel);
  if (!existsSync(file)) {
    return null;
  }
  return readFileSync(file, "utf8");
}

async function readRemote(rel: string): Promise<string | null> {
  const url = githubRawUrl(rel);
  if (!url) {
    return null;
  }
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    return await response.text();
  } catch {
    return null;
  }
}

async function readText(rel: string): Promise<{ text: string | null; source: string | null }> {
  const local = readLocal(rel);
  if (local !== null) {
    return { text: local, source: "local" };
  }
  const remote = await readRemote(rel);
  return { text: remote, source: remote === null ? null : "github" };
}

function manifestLineFor(rel: string, text: string): string | null {
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) {
      continue;
    }
    const parts = line.split(" ");
    if (parts.length === 3 && parts[1] === rel) {
      return line;
    }
  }
  return null;
}

async function introducingCommit(rel: string): Promise<{
  time: string | null;
  sha: string | null;
  url: string | null;
  error?: string;
}> {
  if (!GITHUB_REPO) {
    return { time: null, sha: null, url: null, error: "Set SCORES_GITHUB_REPO to look up commit time." };
  }
  const url = `https://api.github.com/repos/${GITHUB_REPO}/commits?path=${encodeURIComponent(rel)}&sha=${encodeURIComponent(GITHUB_BRANCH)}&per_page=100`;
  try {
    const response = await fetch(url, {
      cache: "no-store",
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!response.ok) {
      return { time: null, sha: null, url: null, error: `GitHub commits API returned ${response.status}.` };
    }
    const commits = (await response.json()) as Array<{
      sha: string;
      html_url: string;
      commit?: { committer?: { date?: string } };
    }>;
    if (!Array.isArray(commits) || commits.length === 0) {
      return { time: null, sha: null, url: null, error: "No commit on GitHub for that path." };
    }
    const first = commits[commits.length - 1];
    return {
      time: first.commit?.committer?.date ?? null,
      sha: first.sha,
      url: first.html_url,
    };
  } catch {
    return { time: null, sha: null, url: null, error: "Could not reach the GitHub commits API." };
  }
}

export async function POST(request: Request) {
  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return NextResponse.json({ error: "Send a JSON body with a line." }, { status: 400 });
  }
  const raw = typeof body.line === "string" ? body.line : "";
  if (!raw.trim()) {
    return NextResponse.json({ error: "Paste a forecast line." }, { status: 400 });
  }
  let parsed;
  try {
    parsed = parseForecastLine(raw);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not parse that line." },
      { status: 400 },
    );
  }
  const canonical = canonicalForecastLine(parsed);
  const rel = forecastRel(parsed.issue_time);
  const lineHash = digest(raw.trim());
  const canonicalLineHash = digest(`${canonical}\n`);
  const errors: string[] = [];
  const file = await readText(rel);
  const fileHash = file.text === null ? null : digest(file.text);
  const lineInFile =
    file.text === null ? null : file.text.split(/\r?\n/).some((row) => row === canonical || row === raw.trim());
  if (file.text === null) {
    errors.push(`Could not read ${rel} locally or from GitHub raw.`);
  } else if (!lineInFile) {
    errors.push("That canonical line is not in the hour file.");
  }
  const manifest = await readText("data/manifest/hashes.log");
  const manifestEntry = manifest.text ? manifestLineFor(rel, manifest.text) : null;
  let manifestMatches: boolean | null = null;
  if (manifestEntry && fileHash) {
    const recorded = manifestEntry.split(" ")[2];
    manifestMatches = recorded === fileHash;
    if (!manifestMatches) {
      errors.push("File hash does not match the manifest line.");
    }
  } else if (!manifestEntry) {
    errors.push("No hashes.log line for that path.");
  }
  const commit = await introducingCommit(rel);
  if (commit.error) {
    errors.push(commit.error);
  }
  return NextResponse.json({
    path: rel,
    line_hash: lineHash,
    canonical_line_hash: canonicalLineHash,
    file_hash: fileHash,
    line_in_file: lineInFile,
    manifest_entry: manifestEntry,
    manifest_matches: manifestMatches,
    commit_time: commit.time,
    commit_sha: commit.sha,
    commit_url: commit.url,
    errors,
  });
}
