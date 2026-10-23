"use client";

import { useWatchlist } from "@/lib/watchlist";

export function Star({ slug, name }: { slug: string; name: string }) {
  const { has, toggle } = useWatchlist();
  const on = has(slug);
  return (
    <button
      type="button"
      className="star"
      aria-pressed={on}
      aria-label={`${on ? "Remove" : "Add"} ${name} ${on ? "from" : "to"} watchlist`}
      onClick={() => toggle(slug)}
    >
      {on ? "★" : "☆"}
    </button>
  );
}
