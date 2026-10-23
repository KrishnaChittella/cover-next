"use client";

import { GameRow } from "@/components/GameRow";
import type { Game } from "@/lib/site";

export function RowList({ games }: { games: Game[] }) {
  return (
    <ol className="rows">
      {games.map((g, i) => (
        <GameRow key={g.slug} game={g} rank={i + 1} />
      ))}
    </ol>
  );
}
