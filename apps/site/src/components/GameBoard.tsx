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

function topViews(game: Game): number | null {
  return game.youtube.state === "ok" ? (game.youtube.top_videos[0]?.views ?? null) : null;
}

/** One plain sentence from real numbers: what moved, or what is being watched. */
function trigger(game: Game, change: number | null): string {
  if (change !== null && Math.abs(change) >= 0.2) {
    return `Wikipedia views ${formatChange(change)}`;
  }
  const top = game.youtube.state === "ok" ? game.youtube.top_videos[0] : undefined;
  if (top) return `Most-watched video this week: ${top.title}`;
  return change !== null ? `Wikipedia views ${formatChange(change)}` : "No reading this week";
}

export function GameBoard({ games }: { games: Game[] }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("change");
  const [filter, setFilter] = useState<Filter>("all");
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => setWatchlist(readWatchlist()), []);

  function toggleWatch(slug: string) {
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
    rows.sort((a, b) => {
      if (sort === "name") return a.game.name.localeCompare(b.game.name);
      if (sort === "wikipedia") return byDesc(a.game.wikipedia.last_7_days, b.game.wikipedia.last_7_days);
      if (sort === "youtube") return byDesc(topViews(a.game), topViews(b.game));
      return byDesc(a.change, b.change);
    });
    return rows;
  }, [games, query, sort, filter, watchlist]);

  const filters: { key: Filter; label: string }[] = [
    { key: "all", label: "All games" },
    { key: "rising", label: "Rising curiosity" },
    { key: "watchlist", label: `Watchlist (${watchlist.length})` },
  ];

  return (
    <>
      <div className="controls">
        <div className="chips" role="group" aria-label="Filter games">
          {filters.map((f) => (
            <button key={f.key} type="button" className="chip" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="tools">
          <input
            type="search"
            placeholder="Search games"
            aria-label="Search games"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label className="sort">
            <span className="label">Sort:</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="change">Biggest change in curiosity</option>
              <option value="wikipedia">Most Wikipedia views</option>
              <option value="youtube">Most-watched YouTube video</option>
              <option value="name">Name</option>
            </select>
          </label>
        </div>
      </div>

      <div className="list-head">
        <h2>Games this week</h2>
        <span className="label">Hover or tap a game for details</span>
      </div>

      {shown.length === 0 ? (
        <p className="empty">
          {filter === "watchlist"
            ? "Your watchlist is empty. Tap the star on a game to keep it here. It is saved in this browser only."
            : "No game matches."}
        </p>
      ) : (
        <ol className="rows">
          {shown.map(({ game, trend, change }, index) => {
            const watched = watchlist.includes(game.slug);
            const expanded = open === game.slug;
            const top = topViews(game);
            return (
              <li key={game.slug} className={`row ${trend}`} data-open={expanded}>
                <div className="row-main">
                  <button
                    type="button"
                    className="row-toggle"
                    aria-expanded={expanded}
                    aria-controls={`d-${game.slug}`}
                    onClick={() => setOpen(expanded ? null : game.slug)}
                  >
                    <span className="rank mono" aria-hidden="true">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="row-title">
                      <span className="name">{game.name}</span>
                      <span className="trigger">{trigger(game, change)}</span>
                    </span>
                  </button>
                  <div className="row-side">
                    <span className="metric">
                      <span className="label">Wikipedia 7d</span>
                      <span className="mono value">{formatCount(game.wikipedia.last_7_days)}</span>
                    </span>
                    <span className={`pill ${trend}`}>{TREND_LABEL[trend]}</span>
                    <button
                      type="button"
                      className="star"
                      aria-pressed={watched}
                      aria-label={`${watched ? "Remove" : "Add"} ${game.name} ${watched ? "from" : "to"} watchlist`}
                      onClick={() => toggleWatch(game.slug)}
                    >
                      {watched ? "★" : "☆"}
                    </button>
                  </div>
                </div>

                <div className="strip mono">
                  <span>
                    <span className="k">Wikipedia:</span> <span className="v">{formatCount(game.wikipedia.last_7_days)}</span>
                  </span>
                  <span>
                    <span className="k">Change:</span>{" "}
                    <span className={`v ${trend}`}>
                      {change === null ? "no comparison" : `${change > 0 ? "+" : ""}${Math.round(change * 100)}%`}
                    </span>
                  </span>
                  <span>
                    <span className="k">Top video:</span> <span className="v">{top === null ? "not measured" : `${formatCount(top)} views`}</span>
                  </span>
                  <span>
                    <span className="k">Steam / Twitch:</span> <span className="v muted">pending</span>
                  </span>
                </div>

                <div className="details" id={`d-${game.slug}`}>
                  <div className="details-inner">
                    <section>
                      <span className="label">Daily Wikipedia views</span>
                      {game.wikipedia.state === "ok" ? (
                        <Sparkline days={game.wikipedia.daily} label={`Daily Wikipedia views for ${game.name}`} />
                      ) : (
                        <p className="missing">Not measured: no Wikipedia article is tracked for this game.</p>
                      )}
                    </section>
                    <section>
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
                        <p className="missing">Not measured yet.</p>
                      )}
                      <span className="sub">From YouTube</span>
                    </section>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}
