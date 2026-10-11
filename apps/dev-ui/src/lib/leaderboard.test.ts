import { describe, expect, it } from "vitest";

import { current } from "@/test/bundle";

import { DEFAULT_QUERY, applyQuery, distinct, matchesSearch } from "./leaderboard";

const entries = current().entries;
const slugs = (rows: { slug: string }[]) => rows.map((row) => row.slug);

describe("ordering", () => {
  it("defaults to the artifact's own rank order and changes nothing", () => {
    const rows = applyQuery(entries, DEFAULT_QUERY);
    expect(slugs(rows)).toEqual(slugs([...entries]));
    expect(rows.map((row) => row.rank)).toEqual(entries.map((_, index) => index + 1));
    expect(rows.map((row) => row.score)).toEqual(entries.map((entry) => entry.score));
  });

  it("does not reorder or mutate the artifact's list", () => {
    const before = slugs([...entries]);
    applyQuery(entries, { ...DEFAULT_QUERY, sort: "name", direction: "desc" });
    expect(slugs([...entries])).toEqual(before);
  });

  it("sorts by score, highest first, and keeps rank order among equal scores", () => {
    const rows = applyQuery(entries, { ...DEFAULT_QUERY, sort: "score", direction: "desc" });
    for (let index = 1; index < rows.length; index += 1) {
      const above = rows[index - 1]!;
      const below = rows[index]!;
      expect(above.score).toBeGreaterThanOrEqual(below.score);
      if (above.score === below.score) expect(above.rank).toBeLessThan(below.rank);
    }
  });

  it("keeps rank order among ties whichever way a column is sorted", () => {
    const tied = entries.filter((entry) => entry.score === 0);
    expect(tied.length).toBeGreaterThan(1); // the fixture has several games at zero
    const ascending = applyQuery(entries, { ...DEFAULT_QUERY, sort: "score", direction: "asc" });
    expect(slugs(ascending.filter((entry) => entry.score === 0))).toEqual(slugs(tied));
  });

  it("sorts by name without regard to case", () => {
    const rows = applyQuery(entries, { ...DEFAULT_QUERY, sort: "name", direction: "asc" });
    const names = rows.map((row) => row.name);
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" })));
  });
});

describe("search and filters", () => {
  it("matches every word, in any order, against the name or the slug", () => {
    expect(matchesSearch("", "Minecraft", "minecraft")).toBe(true);
    expect(matchesSearch("  ", "Minecraft", "minecraft")).toBe(true);
    expect(matchesSearch("MINE", "Minecraft", "minecraft")).toBe(true);
    expect(matchesSearch("auto theft", "Grand Theft Auto V", "gta-v")).toBe(true);
    expect(matchesSearch("gta v", "Grand Theft Auto V", "gta-v")).toBe(true);
    expect(matchesSearch("baldurs gate", "Baldur's Gate 3", "baldurs-gate-3")).toBe(true);
    expect(matchesSearch("pokemon", "Pokémon Legends", "pokemon-legends")).toBe(true);
    expect(matchesSearch("zelda", "Minecraft", "minecraft")).toBe(false);
    expect(matchesSearch("mine zelda", "Minecraft", "minecraft")).toBe(false);
  });

  it("narrows by search and keeps rank order", () => {
    const rows = applyQuery(entries, { ...DEFAULT_QUERY, search: "grand theft" });
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((row) => row.name.toLowerCase().includes("grand theft"))).toBe(true);
    expect(rows.map((row) => row.rank)).toEqual([...rows.map((row) => row.rank)].sort((a, b) => a - b));
  });

  it("filters by lifecycle and by confidence band, and by both", () => {
    const live = applyQuery(entries, { ...DEFAULT_QUERY, lifecycle: "LIVE_SERVICE" });
    expect(live.length).toBe(entries.filter((entry) => entry.lifecycle_state === "LIVE_SERVICE").length);
    expect(live.every((row) => row.lifecycle_state === "LIVE_SERVICE")).toBe(true);

    const medium = applyQuery(entries, { ...DEFAULT_QUERY, band: "medium" });
    expect(medium.length).toBe(entries.filter((entry) => entry.confidence_band === "medium").length);

    expect(applyQuery(entries, { ...DEFAULT_QUERY, band: "high" })).toEqual([]);
    expect(applyQuery(entries, { ...DEFAULT_QUERY, lifecycle: "LIVE_SERVICE", band: "high" })).toEqual([]);
    expect(applyQuery(entries, { ...DEFAULT_QUERY, search: "no such game" })).toEqual([]);
  });

  it("offers only the filter values that are present", () => {
    expect(distinct(entries.map((entry) => entry.confidence_band))).toEqual(["medium"]);
    expect(distinct(entries.map((entry) => entry.lifecycle_state))).toEqual(
      [...new Set(entries.map((entry) => entry.lifecycle_state))].sort(),
    );
  });
});
