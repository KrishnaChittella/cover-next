"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";

import type { LeaderboardEntry } from "@/contract";
import { freshnessSummary } from "@/lib/coverage";
import { bandLabel, formatConfidence, formatScore, lifecycleLabel } from "@/lib/format";
import {
  DEFAULT_QUERY,
  NATURAL_DIRECTION,
  applyQuery,
  distinct,
  type LeaderboardQuery,
  type SortKey,
} from "@/lib/leaderboard";

import { BandBadge, StatusBadge } from "./ui";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "rank", label: "Rank" },
  { key: "score", label: "Score" },
  { key: "confidence", label: "Confidence" },
  { key: "name", label: "Name" },
];

const controlClass =
  "h-9 rounded border border-line-strong bg-raised px-2.5 text-sm text-ink placeholder:text-faint hover:border-muted";

function ScoreCell({ score }: { score: number }) {
  return (
    <div className="flex items-center justify-end gap-3">
      <div aria-hidden="true" className="hidden h-1.5 w-16 overflow-hidden rounded-sm bg-raised lg:block">
        <div className="h-full bg-signal" style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
      </div>
      <span className="num font-mono text-base font-semibold text-ink">{formatScore(score)}</span>
    </div>
  );
}

function SortHeader({
  column,
  label,
  query,
  onSort,
  align = "left",
}: {
  column: SortKey;
  label: string;
  query: LeaderboardQuery;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = query.sort === column;
  return (
    <th
      scope="col"
      aria-sort={active ? (query.direction === "asc" ? "ascending" : "descending") : "none"}
      className={`px-3 py-2 font-medium ${align === "right" ? "text-right" : "text-left"}`}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex items-center gap-1 rounded hover:text-ink ${active ? "text-ink" : ""}`}
      >
        {label}
        <span aria-hidden="true" className="w-2 text-[0.6rem]">
          {active ? (query.direction === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
    </th>
  );
}

export function Leaderboard({ entries }: { entries: readonly LeaderboardEntry[] }) {
  const [query, setQuery] = useState<LeaderboardQuery>(DEFAULT_QUERY);
  const ids = useId();
  const lifecycles = useMemo(() => distinct(entries.map((entry) => entry.lifecycle_state)), [entries]);
  const bands = useMemo(() => distinct(entries.map((entry) => entry.confidence_band)), [entries]);
  const rows = useMemo(() => applyQuery(entries, query), [entries, query]);
  const filtered = query.search !== "" || query.lifecycle !== null || query.band !== null;

  const sortBy = (key: SortKey) =>
    setQuery((previous) =>
      previous.sort === key
        ? { ...previous, direction: previous.direction === "asc" ? "desc" : "asc" }
        : { ...previous, sort: key, direction: NATURAL_DIRECTION[key] },
    );

  if (entries.length === 0) {
    return (
      <p className="panel p-5 text-sm text-ink-soft">
        No game is ranked in this run. See the unranked games below for the reason each has no score.
      </p>
    );
  }

  return (
    <div>
      <form
        role="search"
        aria-label="Filter the leaderboard"
        onSubmit={(event) => event.preventDefault()}
        className="mb-3 flex flex-wrap items-end gap-x-3 gap-y-3"
      >
        <div className="min-w-48 flex-1">
          <label htmlFor={`${ids}-search`} className="eyebrow mb-1 block">
            Search
          </label>
          <input
            id={`${ids}-search`}
            type="search"
            value={query.search}
            onChange={(event) => setQuery({ ...query, search: event.target.value })}
            placeholder="Game name"
            autoComplete="off"
            className={`${controlClass} w-full`}
          />
        </div>
        <div>
          <label htmlFor={`${ids}-lifecycle`} className="eyebrow mb-1 block">
            Lifecycle
          </label>
          <select
            id={`${ids}-lifecycle`}
            value={query.lifecycle ?? ""}
            onChange={(event) => setQuery({ ...query, lifecycle: event.target.value || null })}
            className={controlClass}
          >
            <option value="">All</option>
            {lifecycles.map((state) => (
              <option key={state} value={state}>
                {lifecycleLabel(state)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${ids}-band`} className="eyebrow mb-1 block">
            Confidence
          </label>
          <select
            id={`${ids}-band`}
            value={query.band ?? ""}
            onChange={(event) => setQuery({ ...query, band: event.target.value || null })}
            className={controlClass}
          >
            <option value="">All</option>
            {bands.map((band) => (
              <option key={band} value={band}>
                {bandLabel(band)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${ids}-sort`} className="eyebrow mb-1 block">
            Sort by
          </label>
          <select
            id={`${ids}-sort`}
            value={query.sort}
            onChange={(event) => {
              const key = event.target.value as SortKey;
              setQuery({ ...query, sort: key, direction: NATURAL_DIRECTION[key] });
            }}
            className={controlClass}
          >
            {SORTS.map((sort) => (
              <option key={sort.key} value={sort.key}>
                {sort.label}
              </option>
            ))}
          </select>
        </div>
        {filtered || query.sort !== "rank" ? (
          <button type="button" onClick={() => setQuery(DEFAULT_QUERY)} className={`${controlClass} text-ink-soft`}>
            Reset
          </button>
        ) : null}
      </form>

      <p role="status" aria-live="polite" className="mb-2 text-xs text-muted">
        Showing {rows.length} of {entries.length} ranked game{entries.length === 1 ? "" : "s"}
        {query.sort === "rank" && query.direction === "asc" ? ", in artifact rank order" : ""}.
      </p>

      {rows.length === 0 ? (
        <p className="panel p-5 text-sm text-ink-soft">No ranked game matches these filters.</p>
      ) : (
        <>
          {/* Wide screens: a real table. */}
          <div className="panel hidden overflow-x-auto md:block">
            <table data-testid="leaderboard-table" className="w-full border-collapse text-sm">
              <caption className="sr-only">
                Ranked games by {query.sort}, {query.direction === "asc" ? "ascending" : "descending"}
              </caption>
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <SortHeader column="rank" label="Rank" query={query} onSort={sortBy} align="right" />
                  <SortHeader column="name" label="Game" query={query} onSort={sortBy} />
                  <SortHeader column="score" label="Score v0" query={query} onSort={sortBy} align="right" />
                  <SortHeader column="confidence" label="Confidence" query={query} onSort={sortBy} align="right" />
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    Why now
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    Freshness
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((entry) => {
                  const freshness = freshnessSummary(entry.source_coverage);
                  return (
                    <tr key={entry.slug} className="border-b border-line align-top last:border-b-0 hover:bg-raised/60">
                      <td className="num w-12 px-3 py-3 text-right font-mono text-muted">{entry.rank}</td>
                      <th scope="row" className="px-3 py-3 text-left font-normal">
                        <Link
                          href={`/games/${entry.slug}`}
                          className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
                        >
                          {entry.name}
                        </Link>
                        <span className="mt-1 block text-xs text-muted">{lifecycleLabel(entry.lifecycle_state)}</span>
                      </th>
                      <td className="px-3 py-3 text-right">
                        <ScoreCell score={entry.score} />
                      </td>
                      <td className="px-3 py-3 text-right">
                        <span className="num font-mono text-ink">{formatConfidence(entry.confidence)}</span>
                        <span className="mt-1 flex justify-end">
                          <BandBadge band={entry.confidence_band} label={bandLabel(entry.confidence_band)} />
                        </span>
                      </td>
                      <td className="max-w-md px-3 py-3 text-ink-soft">{entry.summary}</td>
                      <td className="px-3 py-3 whitespace-nowrap">
                        <StatusBadge status={freshness.status} label={freshness.label} />
                        <span className="mt-1 block text-xs text-muted">{freshness.detail}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Narrow screens: the same rows as a list, not a squeezed table. */}
          <ol data-testid="leaderboard-list" className="space-y-2 md:hidden">
            {rows.map((entry) => {
              const freshness = freshnessSummary(entry.source_coverage);
              return (
                <li key={entry.slug} className="panel p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <p className="num font-mono text-xs text-muted">Rank {entry.rank}</p>
                      <Link
                        href={`/games/${entry.slug}`}
                        className="mt-0.5 block font-medium text-ink underline decoration-line-strong underline-offset-4"
                      >
                        {entry.name}
                      </Link>
                      <p className="mt-0.5 text-xs text-muted">{lifecycleLabel(entry.lifecycle_state)}</p>
                    </div>
                    <div className="text-right">
                      <p className="eyebrow">Score v0</p>
                      <p className="num font-mono text-xl font-semibold text-ink">{formatScore(entry.score)}</p>
                    </div>
                  </div>
                  <p className="mt-3 text-sm text-ink-soft">{entry.summary}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
                    <span className="num">Confidence {formatConfidence(entry.confidence)}</span>
                    <BandBadge band={entry.confidence_band} label={bandLabel(entry.confidence_band)} />
                    <StatusBadge status={freshness.status} label={freshness.label} />
                  </div>
                </li>
              );
            })}
          </ol>
        </>
      )}
    </div>
  );
}
