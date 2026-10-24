import Link from "next/link";

import { GroupedGames, LeftRail, OpportunityMeter, Spotlight } from "@/components/Rails";
import { RowList } from "@/components/RowList";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { loadSiteData } from "@/lib/load";
import { byRise, folded, formatCount, formatShort, formatUpdated, trendOf } from "@/lib/site";

const CONTENDERS = 15;

export default function Home() {
  const data = loadSiteData();
  const ranked = data.games.filter((g) => g.rise !== null && !folded(g)).sort(byRise);
  const top = ranked.slice(0, CONTENDERS);
  const rising = data.games.filter((g) => trendOf(g) === "rising").length;
  const spot = data.games.find((g) => g.slug === data.spotlight) ?? top[0];
  const live = data.games.filter((g) => g.twitch.state === "ok").sort((a, b) => twitchNow(b) - twitchNow(a))[0];
  const tiles = [
    { k: "Rising today", v: `${rising}`, of: `/ ${data.games.length}`, note: "against their own normal", tone: "primary" },
    {
      k: "Most watched on Twitch",
      v: live ? formatShort(twitchNow(live)) : "-",
      of: "",
      note: live ? live.name : "not read",
      tone: "secondary",
    },
    { k: "Last update", v: formatUpdated(data.generated_at), of: "", note: "daily, around 13:30 UTC", tone: "tertiary" },
  ];
  return (
    <div className="three">
      <LeftRail data={data} />
      <div className="feed">
        <section className="intro hero">
          <div className="intro-text">
            <div className="badge-line">
              <span className="tag">Daily readings</span>
              <span className="label">Wikipedia · YouTube · Steam · Twitch</span>
            </div>
            <h1>
              Which game is worth a video <span className="hot">right now?</span>
            </h1>
            <p>
              Every day Cover Next reads Wikipedia views for all {"30,000"}-odd video games with an English article and
              finds the ones people are suddenly curious about, compared with each game&apos;s own normal level, then
              checks Steam players and Twitch streams for them. {rising} are rising today.
            </p>
          </div>
          <div className="kpis">
            {tiles.map((t) => (
              <div key={t.k} className={`kpi tone-${t.tone}`}>
                <span className="label">{t.k}</span>
                <span className="kpi-v mono">
                  {t.v} <span className="sub">{t.of}</span>
                </span>
                <span className="sub kpi-note">{t.note}</span>
              </div>
            ))}
          </div>
        </section>
        <nav className="chips" aria-label="Browse games">
          <Link className="chip" aria-current="true" href="/games/">
            All games
          </Link>
          <Link className="chip" href="/games/?f=rising">
            Rising ({rising})
          </Link>
          <Link className="chip" href="/games/?f=new">
            New
          </Link>
          <Link className="chip" href="/games/?f=pinned">
            Pinned
          </Link>
          <Link className="chip" href="/games/?f=watchlist">
            Watchlist
          </Link>
        </nav>
        <div className="list-head">
          <h2>Top {CONTENDERS} gaining now</h2>
          <span className="label">Tap a game for details</span>
        </div>
        <RowList games={top} />
        <Link className="see-all" href="/games/">
          See all {data.games.length} games →
        </Link>
        <p className="notice">Early preview. These are readings, not advice: no number here guarantees a video will do well.</p>
      </div>
      <aside className="rail rail-right" aria-label="Watchlist, spotlight, opportunity and grouped games">
        <WatchlistPanel games={data.games} />
        <Spotlight game={spot} />
        <OpportunityMeter data={data} game={spot} />
        <GroupedGames data={data} />
      </aside>
    </div>
  );
}

function twitchNow(g: { twitch: { state: string; now?: number } }): number {
  return g.twitch.state === "ok" ? (g.twitch.now ?? 0) : -1;
}
