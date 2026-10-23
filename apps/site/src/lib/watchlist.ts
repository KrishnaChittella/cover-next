"use client";

import { useCallback, useEffect, useState } from "react";

const KEY = "cover-next:watchlist";
const EVENT = "cover-next:watchlist";

function read(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((s): s is string => typeof s === "string") : [];
  } catch {
    return [];
  }
}

/** Slugs the visitor starred. Kept in this browser only; every open component stays in sync. */
export function useWatchlist(): { list: string[]; has: (slug: string) => boolean; toggle: (slug: string) => void } {
  const [list, setList] = useState<string[]>([]);
  useEffect(() => {
    const sync = () => setList(read());
    sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const toggle = useCallback((slug: string) => {
    const current = read();
    const next = current.includes(slug) ? current.filter((s) => s !== slug) : [...current, slug];
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Storage blocked: the watchlist lasts for this visit only.
    }
    setList(next);
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return { list, has: (slug) => list.includes(slug), toggle };
}
