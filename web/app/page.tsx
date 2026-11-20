import { HomeBoard } from "@/components/home-board";
import { REVALIDATE_SECONDS } from "@/lib/config";
import { loadScoreBundle } from "@/lib/scores";

export const revalidate = REVALIDATE_SECONDS;

export default async function HomePage() {
  const scores = await loadScoreBundle();

  return (
    <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
      <p className="text-sm text-neutral-500">Probabilistic forecasts, sealed before the hour they name.</p>
      <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight sm:text-5xl sm:leading-tight">
        Can AI forecast markets better than a random walk?
      </h1>
      <HomeBoard live={scores.live} retrospective={scores.retrospective} />
    </main>
  );
}
