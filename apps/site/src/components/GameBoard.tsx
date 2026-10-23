"use client";

import { useEffect, useMemo, useState } from "react";

import { Sparkline } from "@/components/Sparkline";
import { formatChange, formatCount, trendOf, type Game, type Trend } from "@/lib/site";

const WATCHLIST_KEY = "cover-next:watchlist";
const TREND_LABEL: Record<Trend, string> = {
  rising: "Rising curiosity",
  steady: "Steady",
  cooling: "Cooling",
  unknown: "No comparison",
};

type Sort = "change" | "wikipedia" | "youtube" | "name";
type Filter = "all" | "rising" | "watchlist";

function readWatchlist(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(WATCHLIST_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((s): s is string => typeof s === "string") : [];
  } catch {
    return [];
  }
}

// Unmeasured values sort last whichever way the list is ordered.
function byDesc(a: number | null, b: number | null): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return b - a;
}

export function GameBoard({ games }: { games: Game[] }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("change");
  const [filter, setFilter] = useState<Filter>("all");
  const [watchlist, setWatchlist] = useState<string[]>([]);

  useEffect(() => setWatchlist(readWatchlist()), []);

  function toggle(slug: string) {
    const next = watchlist.includes(slug) ? watchlist.filter((s) => s !== slug) : [...watchlist, slug];
    setWatchlist(next);
    try {
      localStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
    } catch {
      // Storage blocked: the watchlist lasts for this visit only.
    }
  }

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = games
      .map((game) => ({ game, ...trendOf(game.wikipedia) }))
      .filter(({ game, trend }) => {
        if (needle && !game.name.toLowerCase().includes(needle)) return false;
        if (filter === "rising") return trend === "rising";
        if (filter === "watchlist") return watchlist.includes(game.slug);
        return true;
      });
    const topViews = (g: Game) => (g.youtube.state === "ok" ? (g.youtube.top_videos[0]?.views ?? null) : null);
    rows.sort((a, b) => {
      if (sort === "name") return a.game.name.localeCompare(b.game.name);
      if (sort === "wikipedia") return byDesc(a.game.wikipedia.last_7_days, b.game.wikipedia.last_7_days);
      if (sort === "youtube") return byDesc(topViews(a.game), topViews(b.game));
      return byDesc(a.change, b.change);
    });
    return rows;
  }, [games, query, sort, filter, watchlist]);

  return (
    <>
      <div className="controls">
        <input
          type="search"
          placeholder="Search games"
          aria-label="Search games"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {(["all", "rising", "watchlist"] as const).map((f) => (
          <button key={f} type="button" className="chip" aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f === "all" ? "All games" : f === "rising" ? "Rising curiosity" : `Watchlist (${watchlist.length})`}
          </button>
        ))}
        <select aria-label="Sort games" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
          <option value="change">Biggest change in curiosity</option>
          <option value="wikipedia">Most Wikipedia views</option>
          <option value="youtube">Most-watched YouTube video this week</option>
          <option value="name">Name</option>
        </select>
      </div>

      {shown.length === 0 ? (
        <p className="empty">
          {filter === "watchlist"
            ? "Your watchlist is empty. Tap the star on a game to keep it here. It is saved in this browser only."
            : "No game matches."}
        </p>
      ) : (
        <div className="grid">
          {shown.map(({ game, trend, change }) => (
            <article key={game.slug} className="card" aria-labelledby={`g-${game.slug}`}>
              <div className="card-head">
                <h2 id={`g-${game.slug}`}>{game.name}</h2>
                <button
                  type="button"
                  className="star"
                  aria-pressed={watchlist.includes(game.slug)}
                  aria-label={`${watchlist.includes(game.slug) ? "Remove" : "Add"} ${game.name} ${watchlist.includes(game.slug) ? "from" : "to"} watchlist`}
                  onClick={() => toggle(game.slug)}
                >
                  {watchlist.includes(game.slug) ? "★" : "☆"}
                </button>
              </div>
              <span className={`badge ${trend}`}>{TREND_LABEL[trend]}</span>

              <div className="signal">
                <span className="label">Wikipedia views, last 7 days</span>
                {game.wikipedia.state === "ok" ? (
                  <>
                    <span className="figure">{formatCount(game.wikipedia.last_7_days)}</span>
                    <span className="sub">{formatChange(change)}</span>
                    <Sparkline days={game.wikipedia.daily} label={`Daily Wikipedia views for ${game.name}`} />
                  </>
                ) : (
                  <span className="missing">Not measured: no Wikipedia article is tracked for this game.</span>
                )}
              </div>

              <div className="signal">
                <span className="label">Top YouTube videos this week</span>
                {game.youtube.state === "ok" && game.youtube.top_videos.length > 0 ? (
                  <ol className="videos">
                    {game.youtube.top_videos.map((v) => (
                      <li key={v.id}>
                        <a href={`https://www.youtube.com/watch?v=${v.id}`} rel="noopener">
                          {v.title}
                        </a>
                        <span className="mono"> {formatCount(v.views)} views</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <span className="missing">Not measured yet.</span>
                )}
                <span className="sub">From YouTube</span>
              </div>

              <p className="pending">Steam players and Twitch viewers: waiting for permission to show them.</p>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
