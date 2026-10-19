import type { Metadata } from "next";

import { GamesIndex } from "@/components/games-index";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Games" };

export default function GamesPage() {
  return (
    <>
      <PageTitle
        title="Games"
        lede="Every game in the current run, ranked or not. Open one for its score, its reasons and its history."
      />
      <GamesIndex />
    </>
  );
}
