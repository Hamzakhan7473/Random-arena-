export const SITE_HOST = process.env.NEXT_PUBLIC_SITE_HOST ?? "randomwalkarena.com";
export const SITE_NAME = "Random Walk Arena";
export const GITHUB_REPO = process.env.SCORES_GITHUB_REPO ?? process.env.NEXT_PUBLIC_GITHUB_REPO ?? "";
export const GITHUB_BRANCH =
  process.env.SCORES_GITHUB_BRANCH ?? process.env.NEXT_PUBLIC_GITHUB_BRANCH ?? "main";
export const REVALIDATE_SECONDS = 3600;
export const HORIZONS = ["1h", "4h", "24h"] as const;
export const KNOWN_MODELS = [
  "no-change",
  "ewma-drift",
  "ar1",
  "chronos-bolt-small",
  "chronos-2",
  "timesfm-2.5",
] as const;

export function githubRawUrl(rel: string): string | null {
  if (!GITHUB_REPO) {
    return null;
  }
  return `https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}/${rel}`;
}

export function githubFileUrl(rel: string): string | null {
  if (!GITHUB_REPO) {
    return null;
  }
  return `https://github.com/${GITHUB_REPO}/blob/${GITHUB_BRANCH}/${rel}`;
}

export function siteOrigin(): string {
  return `https://${SITE_HOST}`;
}
