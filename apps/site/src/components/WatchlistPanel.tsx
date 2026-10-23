"use client";

import Link from "next/link";

import { formatRatio, type Game } from "@/lib/site";
import { useWatchlist } from "@/lib/watchlist";

export function WatchlistPanel({ games }: { games: Game[] }) {
  const { list } = useWatchlist();
  const watched = games.filter((g) => list.includes(g.slug));
  return (
    <section className="panel">
      <h2 className="panel-title">Your watchlist</h2>
      {watched.length === 0 ? (
        <p className="sub">Star a game to follow it here. It is saved in this browser only.</p>
      ) : (
        <ul className="mini">
          {watched.map((g) => (
            <li key={g.slug}>
              <Link href={`/games/${g.slug}/`}>{g.name}</Link>
              <span className="mono">{formatRatio(g.rise?.ratio)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
