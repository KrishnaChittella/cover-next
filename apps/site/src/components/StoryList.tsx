import Link from "next/link";

import { formatRatio, type Game, type Story } from "@/lib/site";

/** One card per studio whose games are rising together, members folded inside. */
export function StoryList({ stories, games }: { stories: Story[]; games: Game[] }) {
  if (stories.length === 0) return null;
  const bySlug = new Map(games.map((g) => [g.slug, g]));
  return (
    <section className="panel stories" aria-label="Studio stories">
      <h2 className="panel-title">Studio stories: games rising together</h2>
      {stories.map((s) => {
        const members = s.members.flatMap((m) => bySlug.get(m) ?? []);
        const count = `${members.length} ${s.role === "developer" ? "games by" : "games published by"} ${s.name} rising together`;
        return (
          <article key={s.slug} id={s.slug} className="story">
            <h3 className="story-title">{count}</h3>
            <details>
              <summary>Show the {members.length} games</summary>
              <ul className="mini">
                {members.map((g) => (
                  <li key={g.slug}>
                    <Link href={`/games/${g.slug}/`}>{g.name}</Link>
                    <span className="mono up">{formatRatio(g.rise?.ratio)}</span>
                  </li>
                ))}
              </ul>
            </details>
          </article>
        );
      })}
    </section>
  );
}
