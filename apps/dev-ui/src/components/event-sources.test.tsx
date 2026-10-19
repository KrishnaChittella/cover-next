// Event sources on the Sources page. The real replay bundle has asked no event source,
// and says so. The collected states below are SYNTHETIC and exist only in this file.
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { EventSourceView, Manifest, SourceHealthArtifact } from "@/contract";
import { eventSourceViolations, manifestViolations, privacyViolations, violations } from "@/contract/validate";
import { loadManifest, loadSourceHealth } from "@/lib/data/client";
import { bundleFetcher, manifest, sourceHealth, type Overrides } from "@/test/bundle";

import { RunNote } from "./run-note";
import { SourceHealth } from "./source-health";

const version = manifest().version_path;
const real = sourceHealth();
const steam = real.event_sources[0]!;

function serve(overrides: Overrides = {}) {
  const { fetcher } = bundleFetcher("/data", overrides);
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

/** SYNTHETIC: a source that was asked about two games and promoted one candidate of five. */
function collected(change: (draft: EventSourceView) => void = () => {}): EventSourceView {
  const draft: EventSourceView = {
    ...structuredClone(steam),
    status: "collected",
    fetches_ok: 2,
    fetches_failed: 1,
    fetches_not_sent: 0,
    last_success_at: "2026-10-03T12:00:00Z",
    last_failure_at: "2026-10-03T12:00:01Z",
    games_checked: 2,
    posts_observed: 5,
    candidates: 5,
    candidates_classified: 3,
    candidates_promoted: 1,
    candidates_not_promoted: 4,
    candidates_unresolved: 1,
    refusals: { no_type_evidence: 2, title_says_not_yet: 1, unresolved_game: 1 },
    promoted_event_types: { minor_patch: 1 },
    fetches_after_as_of: 1,
    games: [
      {
        canonical_game_id: "00000000-0000-4000-8000-000000000001",
        slug: "counter-strike-2",
        name: "Counter-Strike 2",
        source_game_ids: ["730"],
        last_checked_at: "2026-10-03T12:00:00Z",
        fetches_ok: 1,
        fetches_failed: 0,
        posts: 4,
        candidates_classified: 2,
        candidates_promoted: 1,
        candidates_not_promoted: 3,
      },
      {
        canonical_game_id: "00000000-0000-4000-8000-000000000002",
        slug: "dota-2",
        name: "Dota 2",
        source_game_ids: ["570"],
        last_checked_at: "2026-10-03T12:00:00Z",
        fetches_ok: 1,
        fetches_failed: 0,
        posts: 0,
        candidates_classified: 0,
        candidates_promoted: 0,
        candidates_not_promoted: 0,
      },
    ],
  };
  change(draft);
  return draft;
}

const withSource = (source: EventSourceView) => (body: unknown) => ({
  ...(body as SourceHealthArtifact),
  event_sources: [source],
});

const privateManifest = (body: unknown): Manifest => ({
  ...(body as Manifest),
  distribution: "private_development_only",
  restricted_sources: [
    {
      source: "steam_news",
      purpose: "event_intelligence",
      cleared_stages: ["private_development"],
      reason: "Derived events are not addressed by the terms.",
    },
  ],
});

describe("the real replay bundle", () => {
  it("names the event source and says it was not collected, with nothing counted", async () => {
    expect(violations("source-health", real)).toEqual([]);
    expect(eventSourceViolations(real)).toEqual([]);
    expect(real.event_sources).toHaveLength(1);
    expect(steam.source).toBe("steam_news");
    expect(steam.status).toBe("not_collected");
    expect(steam.posts_observed + steam.candidates + steam.candidates_promoted).toBe(0);
    expect(manifest().distribution).toBe("internal");
    expect(manifest().restricted_sources).toEqual([]);
    serve();
    render(<SourceHealth />);
    const card = await screen.findByTestId("event-source");
    expect(card).toHaveTextContent("Steam news");
    expect(card).toHaveTextContent("Not collected");
    expect(card).toHaveTextContent("That is not the same as no events.");
    expect(card).toHaveTextContent("event_classification_rules_v1");
    // Every stage is stated, and only private development is cleared.
    const stages = within(card).getByRole("list", { name: "Stage clearance" });
    expect(within(stages).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("Private development: cleared"),
      expect.stringContaining("Private collection: not cleared"),
      expect.stringContaining("Public launch: not cleared"),
      expect.stringContaining("Commercial production: not cleared"),
    ]);
    expect(screen.queryByTestId("private-bundle")).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Event source coverage by game" })).not.toBeInTheDocument();
  });
});

describe("a collected event source (synthetic, tests only)", () => {
  it("keeps posts, candidates and promoted events apart", async () => {
    serve({
      "manifest.json": privateManifest,
      [`${version}/source-health.json`]: withSource(collected()),
    });
    render(<SourceHealth />);
    const card = await screen.findByTestId("event-source");
    expect(card).toHaveTextContent("Collected");
    expect(card).toHaveTextContent("A post is an announcement, not an event.");
    const counts = within(card).getByTestId("event-source-counts");
    const stat = (label: string) => within(counts).getByText(label).closest("div")!;
    expect(stat("Posts observed")).toHaveTextContent("5");
    expect(stat("Candidates classified")).toHaveTextContent("3");
    expect(stat("Promoted to events")).toHaveTextContent("1");
    expect(stat("Promoted to events")).toHaveTextContent("Minor patch 1");
    expect(stat("Not promoted")).toHaveTextContent("4");
    expect(stat("Not promoted")).toHaveTextContent("1 for a game the catalogue does not bind");
    expect(within(card).getByText("Games checked").closest("div")).toHaveTextContent("2");
    expect(within(card).getByText("Checks").closest("div")).toHaveTextContent("1 failed, 0 not sent");
    expect(card).toHaveTextContent("No type evidence2");
    expect(card).toHaveTextContent("Title says not yet1");
    expect(card).toHaveTextContent("1 check was made after the evaluation moment and takes no part in this run.");
    const rows = within(screen.getByRole("table", { name: "Event source coverage by game" })).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByRole("link", { name: "Counter-Strike 2" })).toHaveAttribute("href", "/games/counter-strike-2");
    // A game that was checked and had no post is listed with zeros: it was looked at.
    expect(rows[1]).toHaveTextContent("Dota 2");
    expect(rows[1]!.textContent).toMatch(/0000$/);
  });

  it("marks the bundle as private development only", async () => {
    serve({
      "manifest.json": privateManifest,
      [`${version}/source-health.json`]: withSource(collected()),
    });
    render(<SourceHealth />);
    const notice = await screen.findByTestId("private-bundle");
    expect(notice).toHaveTextContent("Private development only: not for publication");
    expect(notice).toHaveTextContent("Steam news");
    expect(notice).toHaveTextContent("Nothing here may be published.");
    expect(notice).toHaveTextContent("Derived events are not addressed by the terms.");
  });

  it("shows no private notice for an unrestricted bundle", () => {
    render(<RunNote manifest={manifest()} />);
    expect(screen.queryByTestId("private-bundle")).not.toBeInTheDocument();
  });
});

describe("refusing an event source that contradicts itself", () => {
  const cases: [string, (draft: EventSourceView) => void, string][] = [
    ["a post counted as an event", (draft) => void (draft.candidates_promoted = 2), "/event_sources/0/candidates"],
    ["a promotion nothing classified", (draft) => void (draft.candidates_classified = 0), "/event_sources/0/candidates_promoted"],
    ["promoted types that do not add up", (draft) => void (draft.promoted_event_types = {}), "/event_sources/0/promoted_event_types"],
    ["'not collected' with fetches", (draft) => void (draft.status = "not_collected"), "/event_sources/0/status"],
    [
      "a promotion with no successful check",
      (draft) => {
        draft.fetches_ok = 0;
      },
      "/event_sources/0/candidates_promoted",
    ],
  ];

  it.each(cases)("rejects %s", async (_name, change, path) => {
    const { fetcher } = bundleFetcher("/data", {
      "manifest.json": privateManifest,
      [`${version}/source-health.json`]: withSource(collected(change)),
    });
    await expect(loadSourceHealth({ baseUrl: "/data", fetcher })).rejects.toMatchObject({
      kind: "invalid",
      details: expect.arrayContaining([expect.objectContaining({ path })]),
    });
  });

  it("rejects collected Steam data in a bundle that is not marked private", async () => {
    const { fetcher } = bundleFetcher("/data", { [`${version}/source-health.json`]: withSource(collected()) });
    await expect(loadSourceHealth({ baseUrl: "/data", fetcher })).rejects.toMatchObject({
      kind: "invalid",
      details: [expect.objectContaining({ path: "/event_sources/0/source" })],
    });
    expect(privacyViolations(manifest(), { ...real, event_sources: [collected()] })).toHaveLength(1);
    expect(privacyViolations(privateManifest(manifest()), { ...real, event_sources: [collected()] })).toEqual([]);
    // A source that was never asked has put nothing in the bundle, so nothing is restricted.
    expect(privacyViolations(manifest(), real)).toEqual([]);
  });

  it("rejects a manifest whose distribution disagrees with its restricted sources", async () => {
    const lying = (body: unknown) => ({ ...privateManifest(body), distribution: "internal" });
    const { fetcher } = bundleFetcher("/data", { "manifest.json": lying });
    await expect(loadManifest({ baseUrl: "/data", fetcher })).rejects.toMatchObject({ kind: "invalid" });
    expect(manifestViolations({ ...manifest(), distribution: "private_development_only" })).toHaveLength(1);
    expect(manifestViolations(manifest())).toEqual([]);
  });

  it("renders nothing from such a bundle", async () => {
    serve({ [`${version}/source-health.json`]: withSource(collected()) });
    render(<SourceHealth />);
    expect(await screen.findByRole("alert")).toHaveTextContent("An artifact does not match the contract");
    expect(screen.queryByTestId("event-source")).not.toBeInTheDocument();
  });
});
