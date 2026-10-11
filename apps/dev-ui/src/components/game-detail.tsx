"use client";

import Link from "next/link";

import type { GameArtifact } from "@/contract";
import { loadGame } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";
import {
  bandLabel,
  coverageLabel,
  formatConfidence,
  formatCount,
  formatDay,
  formatScore,
  formatStaleness,
  formatTimestamp,
  humanize,
  lifecycleLabel,
  relationshipLabel,
  unscoredLabel,
} from "@/lib/format";

import { DataState } from "./data-state";
import { FeaturePanel } from "./feature-panel";
import { HistoryChart } from "./history-chart";
import { RunNote } from "./run-note";
import { ScopeNotice } from "./scope-notice";
import { Badge, BandBadge, Meter, Section, StatusBadge } from "./ui";

const COMPONENT_HINTS = {
  significance: "How unusual the latest day is against the game's own day-to-day variability.",
  magnitude: "How far the 3-day average sits above the game's own 28-day median.",
  anomaly: "Significance and magnitude together: unusual and large at once.",
  momentum: "Whether attention is still accelerating against the preceding three days.",
} as const;

const FACTOR_HINTS = {
  source_ceiling: "Set by how many independent sources corroborate the reading.",
  freshness: "Falls as the latest observation trails the day it was due.",
  history: "Rises with the length of history available for the game.",
  completeness: "Share of days inside the history that were observed.",
  samples: "Whether the baseline and the recent week have enough observed days.",
} as const;

function Headline({ game }: { game: GameArtifact }) {
  const scored = game.score.status === "scored";
  return (
    <dl className="panel grid grid-cols-2 gap-x-6 gap-y-5 p-5 md:grid-cols-4">
      <div>
        <dt className="eyebrow">Rank</dt>
        <dd className="num mt-1 font-mono text-3xl font-semibold text-ink">
          {game.rank === null || game.rank === undefined ? (
            <span className="text-xl font-medium text-muted">Unranked</span>
          ) : (
            game.rank
          )}
        </dd>
      </div>
      <div>
        <dt className="eyebrow">Score v0</dt>
        <dd className="num mt-1 font-mono text-3xl font-semibold text-ink">
          {scored ? (
            <>
              {formatScore(game.score.value)}
              <span className="ml-1 text-sm font-normal text-muted">/ 100</span>
            </>
          ) : (
            <span className="text-xl font-medium text-muted">Not scored</span>
          )}
        </dd>
        <dd className="mt-1 font-mono text-xs text-muted">{game.score.score_version}</dd>
      </div>
      <div>
        <dt className="eyebrow">Confidence</dt>
        <dd className="num mt-1 font-mono text-3xl font-semibold text-ink">
          {game.confidence ? (
            <>
              {formatConfidence(game.confidence.value)}
              <span className="ml-1 text-sm font-normal text-muted">/ 100</span>
            </>
          ) : (
            <span className="text-xl font-medium text-muted">Not available</span>
          )}
        </dd>
        {game.confidence ? (
          <dd className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
            <BandBadge band={game.confidence.band} label={bandLabel(game.confidence.band)} />
            <span className="num">ceiling {formatConfidence(game.confidence.ceiling)}</span>
          </dd>
        ) : null}
      </div>
      <div>
        <dt className="eyebrow">Lifecycle</dt>
        <dd className="mt-2">
          <Badge>{lifecycleLabel(game.lifecycle_state)}</Badge>
        </dd>
        <dd className="mt-1.5 text-xs text-muted">Updated {formatTimestamp(game.updated_at)}</dd>
      </div>
    </dl>
  );
}

function GameBody({ game }: { game: GameArtifact }) {
  const components = game.score.components;
  const confidence = game.confidence;
  const events = game.events ?? [];
  return (
    <>
      <nav aria-label="Breadcrumb" className="mb-4 text-sm">
        <Link href="/" className="rounded text-muted hover:text-ink">
          <span aria-hidden="true">← </span>Overview
        </Link>
      </nav>

      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">{game.name}</h1>
        {game.aliases.length > 0 ? (
          <p className="mt-1.5 text-sm text-muted">Also known as {game.aliases.join(", ")}</p>
        ) : null}
        {game.relationships.length > 0 ? (
          <ul className="mt-2 space-y-1 text-sm text-ink-soft">
            {game.relationships.map((relationship) => (
              <li key={`${relationship.relationship_type}-${relationship.parent_slug}`}>
                {relationshipLabel(relationship.relationship_type)}{" "}
                <Link
                  href={`/games/${relationship.parent_slug}`}
                  className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
                >
                  {relationship.parent_name}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </header>

      <Headline game={game} />

      {game.score.status === "unscored" ? (
        <div className="panel mt-4 border-l-2 border-l-info px-4 py-3">
          <p className="eyebrow text-info">
            Not scored{game.score.unscored_reason ? `: ${unscoredLabel(game.score.unscored_reason).toLowerCase()}` : ""}
          </p>
          <p className="mt-1.5 max-w-3xl text-sm text-ink-soft">
            This run recorded no score for this game, so it has no rank, no components and no confidence. That is a
            statement about the data available, not about the game.
          </p>
        </div>
      ) : null}

      <Section id="why-now" title="Why now">
        <div className="panel p-5">
          <p className="max-w-3xl text-base text-ink">{game.explanation.summary}</p>
          {game.explanation.reasons.length > 0 ? (
            <ul className="mt-4 space-y-3">
              {game.explanation.reasons.map((reason) => (
                <li key={reason.code} className="grid gap-x-4 gap-y-1 sm:grid-cols-[11rem_minmax(0,1fr)]">
                  <span className="font-mono text-xs text-muted">{reason.code}</span>
                  <span className="text-sm text-ink-soft">{reason.text}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-4 text-xs text-muted">
            Sentences are rendered from computed features by fixed templates. No language model writes them.
          </p>
        </div>
      </Section>

      {components || confidence ? (
        <Section
          id="score"
          title="Score v0 and confidence"
          note="The parts of the internal attention score. They are not components of a final opportunity score, which does not exist yet."
        >
          <div className="grid gap-4 md:grid-cols-2">
            {components ? (
              <div className="panel p-5">
                <h3 className="eyebrow">Score components, each 0 to 1</h3>
                <div className="mt-4 space-y-4">
                  <Meter label="Significance" value={components.significance} hint={COMPONENT_HINTS.significance} />
                  <Meter label="Magnitude" value={components.magnitude} hint={COMPONENT_HINTS.magnitude} />
                  <Meter label="Anomaly" value={components.anomaly} hint={COMPONENT_HINTS.anomaly} />
                  <Meter label="Momentum" value={components.momentum} hint={COMPONENT_HINTS.momentum} />
                </div>
              </div>
            ) : null}
            {confidence ? (
              <div className="panel p-5">
                <h3 className="eyebrow">Confidence factors, each 0 to 1</h3>
                <p className="num mt-2 text-xs text-muted">
                  {formatCount(confidence.independent_sources)} independent source
                  {confidence.independent_sources === 1 ? "" : "s"}; confidence cannot exceed{" "}
                  {formatConfidence(confidence.ceiling)}.
                </p>
                <div className="mt-4 space-y-4">
                  <Meter label="Source ceiling" value={confidence.factors.source_ceiling} hint={FACTOR_HINTS.source_ceiling} />
                  <Meter label="Freshness" value={confidence.factors.freshness} hint={FACTOR_HINTS.freshness} />
                  <Meter label="History" value={confidence.factors.history} hint={FACTOR_HINTS.history} />
                  <Meter label="Completeness" value={confidence.factors.completeness} hint={FACTOR_HINTS.completeness} />
                  <Meter label="Samples" value={confidence.factors.samples} hint={FACTOR_HINTS.samples} />
                </div>
              </div>
            ) : null}
          </div>
        </Section>
      ) : null}

      <Section id="history" title="History">
        {game.history.length === 0 ? (
          <p className="panel p-5 text-sm text-ink-soft">
            This artifact carries no history for this game: no source returned observations for it in this run.
          </p>
        ) : (
          <div className="space-y-4">
            {game.history.map((series) => (
              <HistoryChart
                key={`${series.source}-${series.metric}-${series.granularity}`}
                series={series}
                gameName={game.name}
              />
            ))}
          </div>
        )}
      </Section>

      <Section id="features" title="Attention features">
        {game.features ? (
          <FeaturePanel features={game.features} />
        ) : (
          <p className="panel p-5 text-sm text-ink-soft">No features were computed for this game in this run.</p>
        )}
      </Section>

      <Section id="coverage" title="Source coverage">
        {game.source_coverage.length === 0 ? (
          <p className="panel p-5 text-sm text-ink-soft">No source is recorded for this game.</p>
        ) : (
          <div className="panel overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Source coverage for {game.name}</caption>
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th scope="col" className="px-4 py-2 text-left font-medium">Source</th>
                  <th scope="col" className="px-4 py-2 text-left font-medium">Status</th>
                  <th scope="col" className="px-4 py-2 text-left font-medium">Latest day</th>
                  <th scope="col" className="px-4 py-2 text-left font-medium">Freshness</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Observed</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Missing</th>
                </tr>
              </thead>
              <tbody>
                {game.source_coverage.map((coverage) => (
                  <tr key={coverage.source} className="border-b border-line align-top last:border-b-0">
                    <th scope="row" className="px-4 py-3 text-left font-normal text-ink">
                      {humanize(coverage.source)}
                      {coverage.attribution ? (
                        <span className="mt-1 block max-w-sm text-xs text-muted">{coverage.attribution}</span>
                      ) : null}
                    </th>
                    <td className="px-4 py-3">
                      <StatusBadge status={coverage.status} label={coverageLabel(coverage.status)} />
                    </td>
                    <td className="num px-4 py-3 whitespace-nowrap">{formatDay(coverage.latest_observed_day)}</td>
                    <td className="px-4 py-3 whitespace-nowrap text-ink-soft">{formatStaleness(coverage.staleness_days)}</td>
                    <td className="num px-4 py-3 text-right font-mono">{formatCount(coverage.observed_days ?? 0)}</td>
                    <td className="num px-4 py-3 text-right font-mono">{formatCount(coverage.missing_days ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {events.length > 0 ? (
        <Section id="events" title="Recorded events" note="Events carried by the artifact. They do not enter score v0.">
          <ul className="panel divide-y divide-line">
            {events.map((event) => (
              <li key={`${event.event_type}-${event.starts_on}-${event.title}`} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3 text-sm">
                <span className="num text-muted">
                  {formatDay(event.starts_on)}
                  {event.ends_on ? ` to ${formatDay(event.ends_on)}` : ""}
                </span>
                <span className="text-ink">{event.title}</span>
                <span className="text-xs text-muted">
                  {humanize(event.event_type)} · {humanize(event.source)}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section id="method" title="Method">
        <ScopeNotice scopeNote={game.methodology.scope_note} scoreVersion={game.methodology.score_version} />
        <p className="mt-3 text-sm">
          <Link
            href="/methodology"
            className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
          >
            Read how {game.methodology.score_version} and confidence are computed
          </Link>
        </p>
      </Section>
    </>
  );
}

export function GameDetail({ slug }: { slug: string }) {
  const state = useArtifact(`game:${slug}`, (options) => loadGame(slug, options));
  return (
    <DataState state={state} label="this game" rows={12}>
      {({ manifest, data }) => (
        <>
          <GameBody game={data} />
          <RunNote manifest={manifest} />
        </>
      )}
    </DataState>
  );
}
