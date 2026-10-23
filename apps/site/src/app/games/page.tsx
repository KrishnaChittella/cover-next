import type { Metadata } from "next";

import { GameBrowser } from "@/components/GameBrowser";
import { loadSiteData } from "@/lib/load";
import { formatDay } from "@/lib/site";

export const metadata: Metadata = { title: "All games" };

export default function GamesPage() {
  const data = loadSiteData();
  return (
    <div className="single">
      <section className="intro">
        <div className="intro-text">
          <span className="label">Data for {formatDay(data.day)}</span>
          <h1>All tracked games</h1>
          <p>
            Today&apos;s biggest risers among every video game on English Wikipedia, plus the games
            we always follow. Search, filter and sort; 25 at a time.
          </p>
        </div>
      </section>
      <GameBrowser games={data.games} />
    </div>
  );
}
