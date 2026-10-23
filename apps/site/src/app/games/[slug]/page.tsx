import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Chart } from "@/components/Chart";
import { Star } from "@/components/Star";
import { loadSiteData } from "@/lib/load";
import { TREND_LABEL, formatCount, formatDay, formatRatio, trendOf, trigger } from "@/lib/site";

export const dynamicParams = false;

export function generateStaticParams() {
  return loadSiteData().games.map((g) => ({ slug: g.slug }));
}

type Props = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const game = loadSiteData().games.find((g) => g.slug === slug);
  return { title: game?.name ?? "Game" };
}

export default async function GamePage({ params }: Props) {
  const { slug } = await params;
  const data = loadSiteData();
  const game = data.games.find((g) => g.slug === slug);
  if (!game) notFound();
  const trend = trendOf(game);
  const w = game.wikipedia;
  const tiles = [
    { k: "Lately, per day", v: formatCount(game.rise?.recent), note: "last 3 days" },
    { k: "Normal, per day", v: formatCount(game.rise?.baseline), note: "median of the 28 before" },
    { k: "Against normal", v: formatRatio(game.rise?.ratio), note: game.rise?.new ? "no normal level yet" : "" },
    { k: "Last 7 days", v: formatCount(w.last_7_days), note: `previous 7: ${formatCount(w.previous_7_days)}` },
  ];
  return (
    <div className="single">
      <p className="crumbs label">
        <Link href="/">Home</Link> / <Link href="/games/">All games</Link>
      </p>
      <section className="game-head">
        <div>
          <div className="badge-line">
            <span className={`pill ${trend}`}>{TREND_LABEL[trend]}</span>
            {game.pinned && <span className="pin">Pinned</span>}
          </div>
          <h1>{game.name}</h1>
          <p className="sub">{trigger(game)}</p>
          {game.story && (
            <p className="sub">
              Part of the <Link href={`/#${game.story.slug}`}>{game.story.name} story</Link>: several of its games are rising together.
            </p>
          )}
        </div>
        <Star slug={game.slug} name={game.name} />
      </section>

      <div className="tiles">
        {tiles.map((t) => (
          <div key={t.k} className="tile">
            <span className="label">{t.k}</span>
            <span className="mono tile-v">{t.v}</span>
            {t.note && <span className="sub">{t.note}</span>}
          </div>
        ))}
      </div>

      <section className="panel">
        <h2 className="panel-title">Wikipedia views, last 31 days</h2>
        {w.state === "ok" ? (
          <Chart days={w.daily} baseline={game.rise?.baseline ?? null} label={`Daily Wikipedia views for ${game.name}`} />
        ) : (
          <p className="missing">Not measured: no Wikipedia article is tracked for this game.</p>
        )}
      </section>

      <section className="panel">
        <h2 className="panel-title">Top YouTube videos this week</h2>
        {game.youtube.state === "ok" && game.youtube.top_videos.length > 0 ? (
          <ol className="videos big">
            {game.youtube.top_videos.map((v) => (
              <li key={v.id}>
                <a href={`https://www.youtube.com/watch?v=${v.id}`} rel="noopener">
                  {v.title}
                </a>
                <span className="mono">
                  {" "}
                  {formatCount(v.views)} views · {formatDay(v.published_at.slice(0, 10))}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="missing">Not searched yet. YouTube is checked for the top risers and pinned games each day.</p>
        )}
        <p className="sub">From YouTube.</p>
      </section>

      <section className="panel">
        <h2 className="panel-title">Sources</h2>
        <ul className="links">
          {w.state === "ok" && (
            <li>
              <a href={`https://en.wikipedia.org/wiki/${encodeURIComponent(w.article)}`}>Wikipedia article</a>
            </li>
          )}
          <li>
            <a href={`https://www.wikidata.org/wiki/${game.qid}`}>Wikidata {game.qid}</a>
          </li>
          {game.igdb && (
            <li>
              <a href={`https://www.igdb.com/games/${game.igdb}`}>IGDB</a>
            </li>
          )}
          {game.steam_app && (
            <li>
              <a href={`https://store.steampowered.com/app/${game.steam_app}/`}>Steam store page</a>
            </li>
          )}
        </ul>
        <p className="sub">Steam players and Twitch viewers: waiting for permission to show them.</p>
      </section>
    </div>
  );
}
