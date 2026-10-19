import type { Metadata } from "next";

import { GameDetail } from "@/components/game-detail";

export const metadata: Metadata = { title: "Game" };

export default async function GamePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <GameDetail slug={slug} />;
}
