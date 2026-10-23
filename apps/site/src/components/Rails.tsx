import Link from "next/link";

import { Sparkline } from "@/components/Sparkline";
import { byRise, folded, formatCount, formatDay, formatRatio, trendOf, type Game, type SiteData } from "@/lib/site";

const STATUS_TEXT = { ok: "Live", missing: "No reading", pending: "Waiting for permission" } as const;

export function LeftRail({ data }: { data: SiteData }) {
  const movers = data.games.filter((g) => trendOf(g) === "rising" && !folded(g)).sort(byRise).slice(0, 5);
  const counts = {
    rising: data.games.filter((g) => trendOf(g) === "rising").length,
    new: data.games.filter((g) => trendOf(g) === "new").length,
    pinned: data.games.filter((g) => g.pinned).length,
  };
  const s = data.sources;
  const sources = [
    { name: "Wikipedia", state: s.wikipedia.state, note: s.wikipedia.games ? `${formatCount(s.wikipedia.games)} games read` : "" },
    { name: "YouTube", state: s.youtube.state, note: s.youtube.games ? `${s.youtube.games} games searched` : "" },
    { name: "Steam", state: s.steam.state, note: s.steam.games ? `${s.steam.games} games read` : "" },
    { name: "Twitch", state: s.twitch.state, note: "" },
  ];
  return (
    <aside className="rail rail-left" aria-label="Filters and status">
      <section className="panel">
        <h2 className="panel-title">Curiosity radar</h2>
        <ul className="nav-list">
          <li><Link href="/games/">All tracked games <span className="mono">{data.games.length}</span></Link></li>
          <li><Link href="/games/?f=rising">Rising <span className="mono">{counts.rising}</span></Link></li>
          <li><Link href="/games/?f=new">Newly noticed <span className="mono">{counts.new}</span></Link></li>
          <li><Link href="/games/?f=pinned">Pinned <span className="mono">{counts.pinned}</span></Link></li>
          <li><Link href="/games/?f=watchlist">Your watchlist</Link></li>
        </ul>
      </section>
      <section className="panel">
        <h2 className="panel-title">Velocity movers</h2>
        <ul className="mini">
          {movers.map((g) => (
            <li key={g.slug}>
              <Link href={`/games/${g.slug}/`}>{g.name}</Link>
              <span className="mono up">{formatRatio(g.rise?.ratio)}</span>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel">
        <h2 className="panel-title">Sources</h2>
        <ul className="sources">
          {sources.map((src) => (
            <li key={src.name}>
              <span className={`dot ${src.state}`} aria-hidden="true" />
              <span>{src.name}</span>
              <span className="label">{STATUS_TEXT[src.state]}</span>
              {src.note && <span className="sub">{src.note}</span>}
            </li>
          ))}
        </ul>
        <p className="sub">Data for {formatDay(data.day)}.</p>
      </section>
    </aside>
  );
}

export function Spotlight({ game }: { game: Game | undefined }) {
  if (!game) return null;
  const top = game.youtube.state === "ok" ? game.youtube.top_videos[0] : undefined;
  return (
    <section className="panel spotlight">
      <span className="tag">Breakout spotlight</span>
      <h2 className="spot-name">
        <Link href={`/games/${game.slug}/`}>{game.name}</Link>
      </h2>
      <p className="spot-figure mono">
        {formatRatio(game.rise?.ratio)} <span className="sub">its normal Wikipedia views</span>
      </p>
      <p className="sub">
        {formatCount(game.rise?.recent)} a day lately, usually {formatCount(game.rise?.baseline)}.
      </p>
      {game.wikipedia.state === "ok" && <Sparkline days={game.wikipedia.daily} label={`Daily Wikipedia views for ${game.name}`} />}
      {top && (
        <p className="sub">
          Most-watched video this week:{" "}
          <a href={`https://www.youtube.com/watch?v=${top.id}`} rel="noopener">
            {top.title}
          </a>{" "}
          ({formatCount(top.views)} views)
        </p>
      )}
    </section>
  );
}
