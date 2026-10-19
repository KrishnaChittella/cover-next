"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";

import { loadCurrent } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";
import { formatScore, lifecycleLabel, unscoredLabel } from "@/lib/format";
import { matchesSearch } from "@/lib/leaderboard";

import { DataState } from "./data-state";
import { RunNote } from "./run-note";
import { Badge } from "./ui";

interface Row {
  slug: string;
  name: string;
  lifecycle: string;
  rank: number | null;
  score: number | null;
  reason: string | null;
}

export function GamesIndex() {
  const state = useArtifact("current", loadCurrent);
  const [search, setSearch] = useState("");
  const id = useId();
  return (
    <DataState state={state} label="the games in this run" rows={10}>
      {({ manifest, data }) => (
        <GamesList
          id={id}
          search={search}
          onSearch={setSearch}
          rows={[
            ...data.entries.map<Row>((entry) => ({
              slug: entry.slug,
              name: entry.name,
              lifecycle: entry.lifecycle_state,
              rank: entry.rank,
              score: entry.score,
              reason: null,
            })),
            ...data.unranked.map<Row>((entry) => ({
              slug: entry.slug,
              name: entry.name,
              lifecycle: entry.lifecycle_state,
              rank: null,
              score: null,
              reason: entry.reason,
            })),
          ]}
          footer={<RunNote manifest={manifest} />}
        />
      )}
    </DataState>
  );
}

function GamesList({
  id,
  search,
  onSearch,
  rows,
  footer,
}: {
  id: string;
  search: string;
  onSearch: (value: string) => void;
  rows: Row[];
  footer: React.ReactNode;
}) {
  const shown = useMemo(
    () =>
      rows
        .filter((row) => matchesSearch(search, row.name, row.slug))
        .sort((a, b) => a.name.localeCompare(b.name, "en", { sensitivity: "base" })),
    [rows, search],
  );
  return (
    <>
      <form role="search" onSubmit={(event) => event.preventDefault()} className="mb-3 max-w-md">
        <label htmlFor={`${id}-search`} className="eyebrow mb-1 block">
          Search every game in this run
        </label>
        <input
          id={`${id}-search`}
          type="search"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          placeholder="Game name"
          autoComplete="off"
          className="h-9 w-full rounded border border-line-strong bg-raised px-2.5 text-sm text-ink placeholder:text-faint hover:border-muted"
        />
      </form>
      <p role="status" aria-live="polite" className="mb-2 text-xs text-muted">
        Showing {shown.length} of {rows.length} games, by name.
      </p>
      {shown.length === 0 ? (
        <p className="panel p-5 text-sm text-ink-soft">No game in this run matches that search.</p>
      ) : (
        <ul className="panel divide-y divide-line">
          {shown.map((row) => (
            <li key={row.slug} className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1.5 px-4 py-3">
              <div className="min-w-0">
                <Link
                  href={`/games/${row.slug}`}
                  className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
                >
                  {row.name}
                </Link>
                <span className="ml-3 text-xs text-muted">{lifecycleLabel(row.lifecycle)}</span>
              </div>
              {row.rank !== null ? (
                <p className="num font-mono text-sm text-ink-soft">
                  rank {row.rank} · score v0 <span className="font-semibold text-ink">{formatScore(row.score)}</span>
                </p>
              ) : (
                <Badge>Unranked: {unscoredLabel(row.reason ?? "").toLowerCase()}</Badge>
              )}
            </li>
          ))}
        </ul>
      )}
      {footer}
    </>
  );
}
