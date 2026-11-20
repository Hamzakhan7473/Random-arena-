import type { Metadata } from "next";

import { VerifyForm } from "./verify-form";

export const metadata: Metadata = {
  title: "Verify",
  description: "Hash a forecast line and look up its manifest entry and commit time.",
};

export default function VerifyPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Verify</h1>
      <p className="mt-4 text-neutral-600">
        Paste one JSON forecast line. This page hashes it, finds{" "}
        <code className="rounded bg-neutral-100 px-1 text-sm">data/forecasts/YYYY-MM-DD/HH.jsonl</code>{" "}
        from the issue time, then looks up that file in{" "}
        <code className="rounded bg-neutral-100 px-1 text-sm">data/manifest/hashes.log</code> and the
        git commit that introduced it.
      </p>
      <VerifyForm />
    </main>
  );
}
