"use client";

import Link from "next/link";
import { useState } from "react";

import { Sparkline } from "@/components/Sparkline";
import { Star } from "@/components/Star";
import { TREND_LABEL, formatCount, formatRatio, topViews, trendOf, trigger, type Game } from "@/lib/site";

/** One game: rank, name, why it is moving, its rise, and a drawer on hover, focus or tap. */
export function GameRow({ game, rank }: { game: Game; rank: number }) {
  const [open, setOpen] = useState(false);
  const trend = trendOf(game);
  const top = topViews(game);
  const href = `/games/${game.slug}/`;
  return (
    <li className={`row ${trend}`} data-open={open}>
      <div className="row-main">
        <button
          type="button"
          className="row-toggle"
          aria-expanded={open}
          aria-controls={`d-${game.slug}`}
          onClick={() => setOpen(!open)}
        >
          <span className="rank mono" aria-hidden="true">
            {String(rank).padStart(2, "0")}
          </span>
          <span className="row-title">
            <span className="name">
              {game.name}
              {game.pinned && <span className="pin">Pinned</span>}
            </span>
            <span className="trigger">{trigger(game)}</span>
          </span>
        </button>
        <div className="row-side">
          <span className="metric">
            <span className="label">vs normal</span>
            <span className="mono value">{formatRatio(game.rise?.ratio)}</span>
          </span>
          <span className={`pill ${trend}`}>{TREND_LABEL[trend]}</span>
          <Star slug={game.slug} name={game.name} />
        </div>
      </div>

      <div className="strip mono">
        <span>
          <span className="k">Wiki 7d:</span> <span className="v">{formatCount(game.wikipedia.last_7_days)}</span>
        </span>
        <span>
          <span className="k">Per day:</span> <span className={`v ${trend}`}>{formatCount(game.rise?.recent)}</span>
        </span>
        <span>
          <span className="k">Top video:</span> <span className="v">{top === null ? "not yet" : `${formatCount(top)} views`}</span>
        </span>
        <span>
          <span className="k">Steam / Twitch:</span> <span className="v muted">pending</span>
        </span>
      </div>

      <div className="details" id={`d-${game.slug}`}>
        <div className="details-inner">
          <section>
            <span className="label">Wikipedia views, last 31 days</span>
            {game.wikipedia.state === "ok" ? (
              <Sparkline days={game.wikipedia.daily} label={`Daily Wikipedia views for ${game.name}`} />
            ) : (
              <p className="missing">Not measured.</p>
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
              <p className="missing">Not searched yet. YouTube is checked for the top risers each day.</p>
            )}
            <Link className="more" href={href}>
              Open {game.name} →
            </Link>
          </section>
        </div>
      </div>
    </li>
  );
}
