"use client";

import Link from "next/link";

import type { SourceHealthEntry } from "@/contract";
import { loadSourceHealth } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";
import { formatCount, formatDay, formatStaleness, formatTimestamp, humanize } from "@/lib/format";

import { DataState } from "./data-state";
import { RunNote } from "./run-note";
import { Stat, StatusBadge } from "./ui";

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
          <RunNote manifest={manifest} />
        </>
      )}
    </DataState>
  );
}
