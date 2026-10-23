import Link from "next/link";

import { LeftRail, Spotlight } from "@/components/Rails";
import { RowList } from "@/components/RowList";
import { StoryList } from "@/components/StoryList";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { loadSiteData } from "@/lib/load";
import { byRise, folded, formatUpdated, trendOf } from "@/lib/site";

const CONTENDERS = 15;

export default function Home() {
  const data = loadSiteData();
  const ranked = data.games.filter((g) => g.rise !== null && !folded(g)).sort(byRise);
  const top = ranked.slice(0, CONTENDERS);
  const rising = data.games.filter((g) => trendOf(g) === "rising").length;
  return (
    <div className="three">
      <LeftRail data={data} />
      <div className="feed">
        <section className="intro">
          <div className="intro-text">
            <div className="badge-line">
              <span className="tag">Daily readings</span>
              <span className="label">Updated {formatUpdated(data.generated_at)}</span>
            </div>
            <h1>
              Which game is worth a video <span className="hot">right now?</span>
            </h1>
            <p>
              Every day Cover Next reads Wikipedia views for all {"30,000"}-odd video games with an
              English article and finds the ones people are suddenly curious about, compared with
              each game&apos;s own normal level. {rising} are rising today.
            </p>
          </div>
        </section>
        <p className="notice">
          Early preview. These are readings, not advice. Twitch figures will be added once Twitch
          confirms we may show them.
        </p>
        <StoryList stories={data.stories} games={data.games} />
        <div className="list-head">
          <h2>Top {CONTENDERS} gaining now</h2>
          <span className="label">Hover or tap for details</span>
        </div>
        <RowList games={top} />
        <Link className="see-all" href="/games/">
          See all {data.games.length} games →
        </Link>
      </div>
      <aside className="rail rail-right" aria-label="Watchlist and spotlight">
        <WatchlistPanel games={data.games} />
        <Spotlight game={top[0]} />
      </aside>
    </div>
  );
}
