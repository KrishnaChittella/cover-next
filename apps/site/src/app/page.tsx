import { GameBoard } from "@/components/GameBoard";
import { loadSiteData } from "@/lib/load";
import { formatUpdated } from "@/lib/site";

export default function Home() {
  const data = loadSiteData();
  return (
    <>
      <section className="intro">
        <p className="label">Updated {formatUpdated(data.generated_at)}</p>
        <h1>Which game is worth a video right now?</h1>
        <p>
          For each game: how many people looked it up on Wikipedia this week compared with last
          week, and which YouTube videos about it are being watched. Use it to spot a game people
          are suddenly curious about before everyone else covers it.
        </p>
        <p className="notice">
          Early preview. These are readings, not advice: no single number here tells you a video
          will do well. Steam and Twitch figures will be added once each provider confirms we may
          show them.
        </p>
      </section>
      <GameBoard games={data.games} />
    </>
  );
}
