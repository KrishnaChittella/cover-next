import Link from "next/link";

import type { UnrankedEntry } from "@/contract";
import { freshnessSummary } from "@/lib/coverage";
import { lifecycleLabel, unscoredLabel } from "@/lib/format";

import { Badge, StatusBadge } from "./ui";

/** Games the run could not score, each with the artifact's own reason. Never hidden. */
export function UnrankedList({ entries }: { entries: readonly UnrankedEntry[] }) {
  if (entries.length === 0) {
    return <p className="panel p-5 text-sm text-ink-soft">Every game in this run has a score.</p>;
  }
  return (
    <ul data-testid="unranked-list" className="panel divide-y divide-line">
      {entries.map((entry) => {
        const freshness = freshnessSummary(entry.source_coverage);
        return (
          <li key={entry.slug} className="grid gap-x-6 gap-y-1.5 px-4 py-3 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto]">
            <div className="min-w-0">
              <Link
                href={`/games/${entry.slug}`}
                className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
              >
                {entry.name}
              </Link>
              <p className="mt-0.5 text-xs text-muted">{lifecycleLabel(entry.lifecycle_state)}</p>
            </div>
            <p className="text-sm text-ink-soft">{entry.summary}</p>
            <div className="flex flex-wrap items-start gap-2 md:justify-end">
              <Badge>{unscoredLabel(entry.reason)}</Badge>
              <StatusBadge status={freshness.status} label={freshness.label} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
