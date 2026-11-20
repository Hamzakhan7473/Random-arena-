import type { Metadata } from "next";

import { REVALIDATE_SECONDS } from "@/lib/config";

export const revalidate = REVALIDATE_SECONDS;

export const metadata: Metadata = {
  title: "Methods",
  description: "How Random Walk Arena seals forecasts, hashes them, and scores them.",
};

export default function MethodsPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Methods</h1>
      <p className="mt-4 text-neutral-600">
        This page is the scoring rule. The leaderboard does not pick a winner. A model “beats the
        random walk” only if a specific test says so, and even then that is a statistical claim, not
        a trading recommendation.
      </p>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Precommitment</h2>
        <p className="mt-3 text-neutral-700">
          A forecast is only scored if it existed before its target time. Each UTC hour is one file,{" "}
          <code className="rounded bg-neutral-100 px-1 text-sm">data/forecasts/YYYY-MM-DD/HH.jsonl</code>.
          That file is written once. Later jobs cannot edit it. The issue time is the end of the last
          fully closed hourly candle, so models never see the bar they are asked to predict.
        </p>
        <p className="mt-3 text-neutral-700">
          Git is the public clock. The commit that introduced the file must have a committer time
          strictly earlier than every <code className="rounded bg-neutral-100 px-1 text-sm">target_time</code>{" "}
          inside it. The parent of that commit must already contain the close at issue time, and must
          not contain any later close for that product. If those checks fail, the hour is not scored.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Hashing</h2>
        <p className="mt-3 text-neutral-700">
          When an hour is sealed, the engine computes the SHA-256 of the exact file bytes and appends
          one line to <code className="rounded bg-neutral-100 px-1 text-sm">data/manifest/hashes.log</code>:
        </p>
        <pre className="mt-3 overflow-x-auto rounded-md bg-neutral-100 p-3 text-xs">
          {"<issue_time> data/forecasts/YYYY-MM-DD/HH.jsonl <sha256>"}
        </pre>
        <p className="mt-3 text-neutral-700">
          Existing manifest lines are never rewritten. Anyone can recompute the hash and compare it
          to the log, then compare the git committer time to the targets. The /verify page does the
          first part of that check from a pasted forecast line.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">What is being forecast</h2>
        <p className="mt-3 text-neutral-700">
          Models quote five price quantiles (10%, 25%, 50%, 75%, 90%) for BTC-USD, ETH-USD, and
          SOL-USD at horizons of 1h, 4h, and 24h. Scoring is on log price, so a $1 move in bitcoin
          does not dwarf a $1 move in another asset. The random-walk baseline is NoChange: every
          quantile is the last close, with a spread taken from past h-step log returns.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Relative error versus the random walk</h2>
        <p className="mt-3 text-neutral-700">
          This is MASE of the median: mean absolute log-error of the model, divided by mean absolute
          log-error of NoChange. 1.0 is exactly as accurate as the random walk. Below 1.0 is better
          on this metric. Above 1.0 is worse. The vertical line on the board is that 1.0.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">95% CI bar</h2>
        <p className="mt-3 text-neutral-700">
          The bar is a moving-block bootstrap of that MASE ratio. Blocks are 24 hours, 2,000
          replicates, seed 0. The whisker is the 2.5% and 97.5% quantiles of those replicates. If
          the interval covers 1.0, the data have not separated the model from the random walk on
          this sample. Hourly returns are dependent; the block bootstrap is there because a naive
          interval would be too narrow.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Diebold–Mariano p-value</h2>
        <p className="mt-3 text-neutral-700">
          DM tests whether the model’s squared log-error is smaller than NoChange’s. The standard
          error is Newey–West (Bartlett kernel) with lag h−1, which is the usual HAC correction for
          an h-step forecast. The reported p-value is two-sided. A small p-value means the loss
          difference is unlikely under equality, not that the model is useful for trading.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Directional accuracy</h2>
        <p className="mt-3 text-neutral-700">
          Share of hours where the median moved the same direction as the realized log price,
          relative to the issue-time close. Ties (zero predicted or realized move) are dropped. The
          engine also stores a binomial p-value versus 50%. A coin flip is 50%. Crypto hours are
          not independent, so that p-value is a rough check, not a license.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Coverage</h2>
        <p className="mt-3 text-neutral-700">
          Fraction of realized log prices that landed between the 10% and 90% quantiles. If the
          80% interval is calibrated, this should sit near 80%. Intervals that are too tight
          under-cover; intervals that are too wide over-cover.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Pinball</h2>
        <p className="mt-3 text-neutral-700">
          Mean pinball loss on log price at the five published quantile levels. This is the same
          number as pinball on the log return versus the issue-time close. Lower is better. It is
          stored on every cell even though the home table leads with MASE.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">The SPA test</h2>
        <p className="mt-3 text-neutral-700">
          Hansen’s Superior Predictive Ability test asks a stricter question than DM: after looking
          at several models, is any of them better than NoChange in expected squared log-error? The
          engine uses <code className="rounded bg-neutral-100 px-1 text-sm">arch.bootstrap.SPA</code>{" "}
          with NoChange as the benchmark, a moving block of 24, 1,000 replicates, seed 0. The null
          is that no alternative beats NoChange.{" "}
          <code className="rounded bg-neutral-100 px-1 text-sm">beats_random_walk</code> is true only
          when that test’s consistent p-value is below 5% and SPA lists the model as better. SPA is
          computed per product and horizon on timestamps that every model in that cell has scored.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Live versus retrospective</h2>
        <p className="mt-3 text-neutral-700">
          Live forecasts are the sealed hourly files. Retrospective forecasts are a walk-forward
          after every model’s published training cutoff, issued once a day, stored under{" "}
          <code className="rounded bg-neutral-100 px-1 text-sm">data/retrospective/</code>, and always
          labeled RETROSPECTIVE. They are not live and not precommitted. They exist to ask how the
          same models would have scored on a past window without pretending those files were hashed
          before the hour.
        </p>
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold tracking-tight">Known limitations</h2>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-neutral-700">
          <li>Hourly crypto is a narrow, noisy task. Results do not transfer to other markets.</li>
          <li>n is small at the start. Bootstrap intervals and SPA have little power until hundreds of hours exist.</li>
          <li>
            Foundation models were trained on other time series. We refuse backfill before the latest
            published cutoff, but a cutoff is only as good as the model card.
          </li>
          <li>NoChange is a strong baseline on prices that wander. Beating it is hard; failing to beat it is common.</li>
          <li>DM, binomial direction tests, and SPA assume more regularity than financial returns have.</li>
          <li>The board averages across products with n-weights. A model can look fine in aggregate and fail on one coin.</li>
          <li>This is not investment advice, a market-making system, or a claim that anyone can harvest alpha from these files.</li>
        </ul>
      </section>
    </main>
  );
}
