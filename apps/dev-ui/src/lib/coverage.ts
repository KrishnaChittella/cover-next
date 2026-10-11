// Reading a game's source coverage. With several sources the least fresh one decides
// how the game is described, so nothing looks fresher than it is.
import type { SourceCoverage } from "@/contract";

import { coverageLabel, formatDay, formatStaleness } from "./format";

export interface FreshnessSummary {
  status: string;
  label: string;
  detail: string;
}

export function freshnessSummary(coverage: readonly SourceCoverage[]): FreshnessSummary {
  if (coverage.length === 0) return { status: "not_mapped", label: "No source", detail: "No source covers this game." };
  const covered = coverage.filter((item) => item.status === "covered");
  if (covered.length === 0) {
    const first = coverage[0] as SourceCoverage;
    return { status: first.status, label: coverageLabel(first.status), detail: "No observation in this run." };
  }
  const worst = covered.reduce((a, b) => ((b.staleness_days ?? Infinity) > (a.staleness_days ?? Infinity) ? b : a));
  return {
    status: "covered",
    label: formatStaleness(worst.staleness_days),
    detail: worst.latest_observed_day ? `Latest day ${formatDay(worst.latest_observed_day)}` : "No observed day",
  };
}
