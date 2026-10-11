"use client";

import Link from "next/link";

import type { EventSourceView, SourceHealthEntry } from "@/contract";
import { loadSourceHealth } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";
import { formatCount, formatDay, formatStaleness, formatTimestamp, humanize } from "@/lib/format";

import { DataState } from "./data-state";
import { RunNote } from "./run-note";
import { Badge, Section, Stat, StatusBadge } from "./ui";

const STATUS_MEANING: Record<string, string> = {
  healthy: "Every mapped game was collected.",
  degraded: "Some games were collected and some failed.",
  failed: "No game was collected from this source.",
};

/** Failures grouped by their error code, so a common cause reads as one line. */
export function failureGroups(source: SourceHealthEntry): { code: string; slugs: string[] }[] {
  const groups = new Map<string, string[]>();
  for (const failure of source.failures) {
    groups.set(failure.error_code, [...(groups.get(failure.error_code) ?? []), failure.slug]);
  }
  return [...groups.entries()]
    .map(([code, slugs]) => ({ code, slugs: slugs.sort() }))
    .sort((a, b) => b.slugs.length - a.slugs.length || a.code.localeCompare(b.code));
}

function SourceCard({ source }: { source: SourceHealthEntry }) {
  const groups = failureGroups(source);
  return (
    <article aria-labelledby={`source-${source.source}`} className="panel p-5">
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <h2 id={`source-${source.source}`} className="text-lg font-semibold tracking-tight text-ink">
          {humanize(source.source)}
          <span className="ml-3 font-mono text-xs font-normal text-muted">{source.source}</span>
        </h2>
        <StatusBadge status={source.status} label={humanize(source.status)} />
      </header>
      {STATUS_MEANING[source.status] ? <p className="mt-1 text-sm text-muted">{STATUS_MEANING[source.status]}</p> : null}

      <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-5 md:grid-cols-4">
        <Stat label="Last success">
          <span className="num">{source.last_success ? formatTimestamp(source.last_success) : "Never in this run"}</span>
        </Stat>
        <Stat label="Expected latest day">
          <span className="num">{formatDay(source.freshness.expected_latest_day)}</span>
        </Stat>
        <Stat label="Latest observed day">
          <span className="num">
            {source.freshness.latest_observed_day ? formatDay(source.freshness.latest_observed_day) : "None observed"}
          </span>
        </Stat>
        <Stat label="Staleness">{formatStaleness(source.freshness.staleness_days)}</Stat>
      </dl>

      <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-5 border-t border-line pt-5 md:grid-cols-4">
        <Stat label="Attempted">
          <span className="num font-mono text-xl">{formatCount(source.entities_attempted)}</span>
        </Stat>
        <Stat label="Succeeded">
          <span className="num font-mono text-xl">{formatCount(source.entities_succeeded)}</span>
        </Stat>
        <Stat label="Failed">
          <span className="num font-mono text-xl">{formatCount(source.entities_failed)}</span>
        </Stat>
        <Stat label="Not mapped" hint="Games with no identifier for this source">
          <span className="num font-mono text-xl">{formatCount(source.entities_not_mapped)}</span>
        </Stat>
      </dl>

      <div className="mt-5 border-t border-line pt-5">
        <h3 className="eyebrow">Failures</h3>
        {groups.length === 0 ? (
          <p className="mt-2 text-sm text-ink-soft">None recorded in this run.</p>
        ) : (
          <ul className="mt-2 space-y-3">
            {groups.map((group) => (
              <li key={group.code} className="text-sm">
                <p>
                  <span className="font-mono text-ink">{group.code}</span>
                  <span className="num ml-2 text-muted">
                    {group.slugs.length} game{group.slugs.length === 1 ? "" : "s"}
                  </span>
                </p>
                <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  {group.slugs.map((slug) => (
                    <li key={slug}>
                      <Link
                        href={`/games/${slug}`}
                        className="font-mono text-xs text-ink-soft underline decoration-line-strong underline-offset-4 hover:text-ink"
                      >
                        {slug}
                      </Link>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}

const EVENT_STATUS: Record<string, { label: string; meaning: string }> = {
  not_collected: {
    label: "Not collected",
    meaning: "This source had not been asked about any game at the evaluation moment. That is not the same as no events.",
  },
  collected: {
    label: "Collected",
    meaning: "The source was asked. A successful check is coverage whether or not it found anything.",
  },
};

function EventSourceCard({ source }: { source: EventSourceView }) {
  const status = EVENT_STATUS[source.status] ?? { label: humanize(source.status), meaning: "" };
  const refusals = Object.entries(source.refusals).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const types = Object.entries(source.promoted_event_types);
  return (
    <article data-testid="event-source" aria-labelledby={`event-source-${source.source}`} className="panel p-5">
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <h3 id={`event-source-${source.source}`} className="text-lg font-semibold tracking-tight text-ink">
          {humanize(source.source)}
          <span className="ml-3 font-mono text-xs font-normal text-muted">{source.source}</span>
        </h3>
        <Badge tone={source.status === "collected" ? "info" : "neutral"}>{status.label}</Badge>
      </header>
      <p className="mt-1 text-sm text-muted">{status.meaning}</p>

      <ul aria-label="Stage clearance" className="mt-4 flex flex-wrap gap-2">
        {source.stage_clearance.map((stage) => (
          <li key={stage.stage} title={stage.reason}>
            <Badge tone={stage.cleared ? "good" : "bad"} glyph>
              {humanize(stage.stage)}: {stage.cleared ? "cleared" : "not cleared"}
            </Badge>
          </li>
        ))}
      </ul>

      <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-5 border-t border-line pt-5 md:grid-cols-4">
        <Stat label="Games checked" hint="With at least one successful check">
          <span className="num font-mono text-xl">{formatCount(source.games_checked)}</span>
        </Stat>
        <Stat label="Last successful check">
          <span className="num">{source.last_success_at ? formatTimestamp(source.last_success_at) : "None"}</span>
        </Stat>
        <Stat label="Checks" hint={`${formatCount(source.fetches_failed)} failed, ${formatCount(source.fetches_not_sent)} not sent`}>
          <span className="num font-mono text-xl">{formatCount(source.fetches_ok)}</span>
        </Stat>
        <Stat label="As of">
          <span className="num">{formatTimestamp(source.as_of)}</span>
        </Stat>
      </dl>

      <p className="mt-5 border-t border-line pt-5 text-sm text-ink-soft">
        A post is an announcement, not an event. Only a promoted candidate becomes an event and can reach Event Heat or
        the radar.
      </p>
      <dl data-testid="event-source-counts" className="mt-4 grid grid-cols-2 gap-x-6 gap-y-5 md:grid-cols-4">
        <Stat label="Posts observed">
          <span className="num font-mono text-xl">{formatCount(source.posts_observed)}</span>
        </Stat>
        <Stat label="Candidates classified" hint="A type of event was recognised">
          <span className="num font-mono text-xl">{formatCount(source.candidates_classified)}</span>
        </Stat>
        <Stat
          label="Promoted to events"
          hint={types.length > 0 ? types.map(([type, count]) => `${humanize(type)} ${count}`).join(", ") : undefined}
        >
          <span className="num font-mono text-xl">{formatCount(source.candidates_promoted)}</span>
        </Stat>
        <Stat label="Not promoted" hint={`${formatCount(source.candidates_unresolved)} for a game the catalogue does not bind`}>
          <span className="num font-mono text-xl">{formatCount(source.candidates_not_promoted)}</span>
        </Stat>
      </dl>

      {refusals.length > 0 ? (
        <div className="mt-5 border-t border-line pt-5">
          <h4 className="eyebrow">Why candidates were not promoted</h4>
          <ul className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            {refusals.map(([reason, count]) => (
              <li key={reason} className="flex justify-between gap-4">
                <span className="text-ink-soft">{humanize(reason)}</span>
                <span className="num font-mono text-muted">{formatCount(count)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {source.games.length > 0 ? (
        <div className="mt-5 overflow-x-auto border-t border-line pt-5">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Event source coverage by game</caption>
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th scope="col" className="py-2 pr-3 text-left font-medium">Game</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Last checked</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Posts</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Classified</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Promoted</th>
                <th scope="col" className="py-2 pl-3 text-right font-medium">Not promoted</th>
              </tr>
            </thead>
            <tbody>
              {source.games.map((game) => (
                <tr key={game.canonical_game_id} className="border-b border-line last:border-b-0">
                  <th scope="row" className="py-2.5 pr-3 text-left font-normal">
                    <Link
                      href={`/games/${game.slug}`}
                      className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
                    >
                      {game.name}
                    </Link>
                  </th>
                  <td className="num px-3 py-2.5 text-ink-soft">
                    {game.last_checked_at ? formatTimestamp(game.last_checked_at) : "Never"}
                  </td>
                  <td className="num px-3 py-2.5 text-right font-mono">{formatCount(game.posts)}</td>
                  <td className="num px-3 py-2.5 text-right font-mono">{formatCount(game.candidates_classified)}</td>
                  <td className="num px-3 py-2.5 text-right font-mono">{formatCount(game.candidates_promoted)}</td>
                  <td className="num py-2.5 pl-3 text-right font-mono">{formatCount(game.candidates_not_promoted)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {source.fetches_after_as_of > 0 ? (
        <p className="mt-4 text-xs text-muted">
          {formatCount(source.fetches_after_as_of)} check{source.fetches_after_as_of === 1 ? " was" : "s were"} made
          after the evaluation moment and {source.fetches_after_as_of === 1 ? "takes" : "take"} no part in this run.
        </p>
      ) : null}
      <p className="mt-4 font-mono text-xs text-faint">{source.classification_rules_version}</p>
    </article>
  );
}

export function SourceHealth() {
  const state = useArtifact("source-health", loadSourceHealth);
  return (
    <DataState state={state} label="source health" rows={8}>
      {({ manifest, data }) => (
        <>
          {data.sources.length === 0 ? (
            <p className="panel p-5 text-sm text-ink-soft">This run reports no source.</p>
          ) : (
            <div className="space-y-4">
              {data.sources.map((source) => (
                <SourceCard key={source.source} source={source} />
              ))}
            </div>
          )}
          <Section
            id="event-sources"
            title="Event sources"
            note="Where announcements are read from. Counts only: no post is shown here."
          >
            {data.event_sources.length === 0 ? (
              <p className="panel p-5 text-sm text-ink-soft">This run reports no event source.</p>
            ) : (
              <div className="space-y-4">
                {data.event_sources.map((source) => (
                  <EventSourceCard key={source.source} source={source} />
                ))}
              </div>
            )}
          </Section>
          <RunNote manifest={manifest} />
        </>
      )}
    </DataState>
  );
}
