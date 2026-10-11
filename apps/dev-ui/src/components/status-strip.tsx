import type { CurrentArtifact, SourceHealthArtifact } from "@/contract";
import { formatConfidence, formatDay, formatStaleness, formatTimestamp, humanize } from "@/lib/format";

import { Stat, StatusBadge } from "./ui";

/** The run this screen was built from, in the artifact's own terms. */
export function StatusStrip({ current, sourceHealth }: { current: CurrentArtifact; sourceHealth: SourceHealthArtifact }) {
  const total = current.entries.length + current.unranked.length;
  return (
    <dl className="panel grid grid-cols-2 gap-x-6 gap-y-5 p-5 md:grid-cols-3 lg:grid-cols-6">
      <Stat label="Generated">
        <span className="num">{formatTimestamp(current.generated_at)}</span>
      </Stat>
      <Stat label="As-of day" hint="Last complete day expected">
        <span className="num">{formatDay(current.expected_latest_day)}</span>
      </Stat>
      <Stat label="Ranked games" hint={`${total - current.entries.length} unranked`}>
        <span className="num">
          {current.entries.length} of {total}
        </span>
      </Stat>
      <Stat label="Active sources">
        {current.active_sources.length === 0 ? (
          "None"
        ) : (
          <ul>
            {current.active_sources.map((source) => (
              <li key={source}>{humanize(source)}</li>
            ))}
          </ul>
        )}
      </Stat>
      <Stat label="Confidence ceiling" hint="Highest confidence any game can have">
        <span className="num">{formatConfidence(current.confidence_ceiling)} / 100</span>
      </Stat>
      <Stat label="Source health">
        {sourceHealth.sources.length === 0 ? (
          "No source reported"
        ) : (
          <ul className="space-y-1.5">
            {sourceHealth.sources.map((source) => (
              <li key={source.source} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <StatusBadge status={source.status} label={humanize(source.status)} />
                <span className="text-xs text-muted">{formatStaleness(source.freshness.staleness_days)}</span>
              </li>
            ))}
          </ul>
        )}
      </Stat>
    </dl>
  );
}
