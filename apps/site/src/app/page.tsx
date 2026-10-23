import { GameBoard } from "@/components/GameBoard";
import { loadSiteData } from "@/lib/load";
import { formatUpdated, trendOf } from "@/lib/site";

export default function Home() {
  const data = loadSiteData();
  const rising = data.games.filter((g) => trendOf(g.wikipedia).trend === "rising").length;
  return (
    <>
      <section className="intro">
        <div className="intro-text">
          <div className="badge-line">
            <span className="tag">Daily readings</span>
            <span className="label">Wikipedia + YouTube</span>
          </div>
          <h1>Which game is worth a video right now?</h1>
          <p>
            For each game: how many people looked it up on Wikipedia this week compared with last
            week, and which YouTube videos about it are being watched. Use it to spot a game people
            are suddenly curious about before everyone else covers it.
          </p>
        </div>
        <div className="sync" aria-label="Freshness">
          <div>
            <span className="label">Rising this week</span>
            <span className="big">
              {rising}/{data.games.length}
            </span>
          </div>
          <span className="rule" aria-hidden="true" />
          <div>
            <span className="label">Updated</span>
            <span className="small">{formatUpdated(data.generated_at)}</span>
          </div>
        </div>
      </section>
      <p className="notice">
        Early preview. These are readings, not advice: no single number here tells you a video will
        do well. Steam and Twitch figures will be added once each provider confirms we may show them.
      </p>
      <GameBoard games={data.games} />
    </>
  );
}
