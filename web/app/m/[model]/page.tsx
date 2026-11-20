import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { HorizonTable } from "@/components/horizon-table";
import { KNOWN_MODELS, REVALIDATE_SECONDS, SITE_HOST } from "@/lib/config";
import { formatCI, formatNumber, slugifyModel } from "@/lib/format";
import { allModels, findModel, modelRows } from "@/lib/horizon";
import { loadScoreBundle } from "@/lib/scores";

export const revalidate = REVALIDATE_SECONDS;

type Params = { model: string };

function catalog(liveModels: string[], retroModels: string[]): string[] {
  return [...new Set([...KNOWN_MODELS, ...liveModels, ...retroModels])];
}

export async function generateStaticParams() {
  const scores = await loadScoreBundle();
  const names = catalog(allModels(scores.live), scores.retrospective ? allModels(scores.retrospective) : []);
  return names.map((model) => ({ model: slugifyModel(model) }));
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const scores = await loadScoreBundle();
  const names = catalog(allModels(scores.live), scores.retrospective ? allModels(scores.retrospective) : []);
  const model = findModel(names, params.model);
  if (!model) {
    return { title: params.model };
  }
  const hours = modelRows(scores.live, model);
  const day = hours["24h"];
  const error = day?.relative_error ?? scores.live.rows.find((row) => row.model === model)?.relative_error ?? null;
  return {
    title: model,
    description: `${model} relative error vs random walk: ${formatNumber(error)}`,
    openGraph: {
      title: model,
      description: `Relative error vs random walk ${formatNumber(error)} · ${SITE_HOST}`,
    },
  };
}

export default async function ModelPage({ params }: { params: Params }) {
  const scores = await loadScoreBundle();
  const names = catalog(allModels(scores.live), scores.retrospective ? allModels(scores.retrospective) : []);
  const model = findModel(names, params.model);
  if (!model) {
    notFound();
  }
  const liveHours = modelRows(scores.live, model);
  const retroHours = scores.retrospective ? modelRows(scores.retrospective, model) : {};
  const liveRows = Object.values(liveHours).filter((row): row is NonNullable<typeof row> => Boolean(row));
  const headline = liveHours["24h"] ?? liveHours["4h"] ?? liveHours["1h"];

  return (
    <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
      <p className="text-sm text-neutral-500">
        <Link href="/" className="hover:underline">
          Arena
        </Link>
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">{model}</h1>
      <p className="mt-3 max-w-2xl text-neutral-600">
        24h relative error versus the random walk
        {headline
          ? ` ${formatNumber(headline.relative_error)} (95% CI ${formatCI(headline.relative_error_ci)}).`
          : " is not scored yet."}{" "}
        Share this page for the Open Graph card.
      </p>
      <HorizonTable horizon="Live" rows={liveRows} />
      {scores.retrospective ? (
        <div className="mt-12">
          <p className="text-sm font-medium uppercase tracking-wide text-neutral-500">Retrospective (labeled)</p>
          <HorizonTable
            horizon="RETROSPECTIVE"
            rows={Object.values(retroHours).filter((row): row is NonNullable<typeof row> => Boolean(row))}
          />
        </div>
      ) : null}
    </main>
  );
}
