"use client";

import { useEffect, useMemo, useState } from "react";

import { GameRow } from "@/components/GameRow";
import { byRise, folded, trendOf, type Game } from "@/lib/site";
import { useWatchlist } from "@/lib/watchlist";

type Filter = "all" | "rising" | "new" | "pinned" | "watchlist";
type Sort = "rise" | "views" | "name";
const PAGE = 25;
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "rising", label: "Rising" },
  { key: "new", label: "New" },
  { key: "pinned", label: "Pinned" },
  { key: "watchlist", label: "Watchlist" },
];

function initialQuery(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("q") ?? "";
}

function initialFilter(): Filter {
  if (typeof window === "undefined") return "all";
  const f = new URLSearchParams(window.location.search).get("f");
  return FILTERS.some((x) => x.key === f) ? (f as Filter) : "all";
}

export function GameBrowser({ games }: { games: Game[] }) {
  const watch = useWatchlist();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("rise");
  const [shown, setShown] = useState(PAGE);

  useEffect(() => {
    setFilter(initialFilter());
    setQuery(initialQuery());
  }, []);
  useEffect(() => setShown(PAGE), [query, filter, sort]);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const kept = games.filter((g) => {
      if (needle && !g.name.toLowerCase().includes(needle)) return false;
      // A studio story's other games stay findable by search, pin and watchlist.
      if (!needle && folded(g) && (filter === "all" || filter === "rising")) return false;
      if (filter === "rising") return trendOf(g) === "rising";
      if (filter === "new") return trendOf(g) === "new";
      if (filter === "pinned") return g.pinned;
      if (filter === "watchlist") return watch.list.includes(g.slug);
      return true;
    });
    const views = (g: Game) => g.wikipedia.last_7_days ?? -1;
    return kept.sort((a, b) =>
      sort === "name" ? a.name.localeCompare(b.name) : sort === "views" ? views(b) - views(a) : byRise(a, b),
    );
  }, [games, query, filter, sort, watch.list]);

  return (
    <>
      <div className="controls">
        <div className="chips" role="group" aria-label="Filter games">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" className="chip" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
              {f.key === "watchlist" ? `Watchlist (${watch.list.length})` : f.label}
            </button>
          ))}
        </div>
        <div className="tools">
          <input type="search" placeholder="Search games" aria-label="Search games" value={query} onChange={(e) => setQuery(e.target.value)} />
          <label className="sort">
            <span className="label">Sort:</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="rise">Biggest rise vs normal</option>
              <option value="views">Most Wikipedia views</option>
              <option value="name">Name</option>
            </select>
          </label>
        </div>
      </div>
      <p className="count label" aria-live="polite">
        {rows.length} {rows.length === 1 ? "game" : "games"}
      </p>
      {rows.length === 0 ? (
        <p className="empty">
          {filter === "watchlist" ? "Your watchlist is empty. Star a game to keep it here; it stays in this browser." : "No game matches."}
        </p>
      ) : (
        <ol className="rows">
          {rows.slice(0, shown).map((g, i) => (
            <GameRow key={g.slug} game={g} rank={i + 1} />
          ))}
        </ol>
      )}
      {shown < rows.length && (
        <button type="button" className="load-more" onClick={() => setShown(shown + PAGE)}>
          Load {Math.min(PAGE, rows.length - shown)} more
        </button>
      )}
    </>
  );
}
