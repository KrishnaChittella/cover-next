// Searching, filtering and sorting the leaderboard. Pure functions over the artifact's
// own entries: nothing is re-scored or re-ranked, and the artifact's rank is the default
// order and the tie-break for every other one.
import type { LeaderboardEntry } from "@/contract";

export type SortKey = "rank" | "score" | "confidence" | "name";
export type SortDirection = "asc" | "desc";

export interface LeaderboardQuery {
  search: string;
  lifecycle: string | null;
  band: string | null;
  sort: SortKey;
  direction: SortDirection;
}

export const DEFAULT_QUERY: LeaderboardQuery = {
  search: "",
  lifecycle: null,
  band: null,
  sort: "rank",
  direction: "asc",
};

/** The direction a column sorts in when it is first chosen. */
export const NATURAL_DIRECTION: Record<SortKey, SortDirection> = {
  rank: "asc",
  score: "desc",
  confidence: "desc",
  name: "asc",
};

function normalise(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** True when every word of the search appears in the name or the slug. */
export function matchesSearch(search: string, name: string, slug: string): boolean {
  const words = normalise(search).split(" ").filter(Boolean);
  if (words.length === 0) return true;
  const haystack = `${normalise(name)} ${normalise(slug)}`;
  return words.every((word) => haystack.includes(word));
}

export function applyQuery(entries: readonly LeaderboardEntry[], query: LeaderboardQuery): LeaderboardEntry[] {
  const kept = entries.filter(
    (entry) =>
      matchesSearch(query.search, entry.name, entry.slug) &&
      (query.lifecycle === null || entry.lifecycle_state === query.lifecycle) &&
      (query.band === null || entry.confidence_band === query.band),
  );
  const sign = query.direction === "asc" ? 1 : -1;
  return kept.sort((a, b) => {
    let order = 0;
    if (query.sort === "score") order = a.score - b.score;
    else if (query.sort === "confidence") order = a.confidence - b.confidence;
    else if (query.sort === "name") order = a.name.localeCompare(b.name, "en", { sensitivity: "base" });
    else order = a.rank - b.rank;
    // Equal values keep the artifact's own order, whichever way the column is sorted.
    return order !== 0 ? order * sign : a.rank - b.rank;
  });
}

/** The values a filter can offer: only those present, in a stable order. */
export function distinct<T extends string>(values: readonly T[]): T[] {
  return [...new Set(values)].sort();
}
