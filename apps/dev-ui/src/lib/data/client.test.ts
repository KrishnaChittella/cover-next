import { describe, expect, it } from "vitest";

import { bundleFetcher, current, manifest } from "@/test/bundle";

import { DataError, loadGame, loadManifest, loadMethodology, loadOverview, loadSourceHealth } from "./client";

const run = manifest().run_id;
const version = manifest().version_path;

async function failure(promise: Promise<unknown>): Promise<DataError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(DataError);
    return error as DataError;
  }
  throw new Error("expected the load to fail");
}

describe("following the manifest", () => {
  it("reads the manifest first and then exactly the versioned files it names", async () => {
    const { fetcher, requested } = bundleFetcher("/data");
    const { manifest: loaded, data } = await loadOverview({ baseUrl: "/data", fetcher });
    expect(loaded.run_id).toBe(run);
    expect(requested[0]).toBe("/data/manifest.json");
    expect(requested.slice(1).sort()).toEqual([`/data/${version}/current.json`, `/data/${version}/source-health.json`]);
    expect(data.current.entries.length).toBe(loaded.ranked_count);
    expect(data.current.entries.length + data.current.unranked.length).toBe(loaded.game_count);
  });

  it("works the same against a remote base URL", async () => {
    const base = "https://cdn.example.test/metaspike";
    const { fetcher, requested } = bundleFetcher(base);
    const { data } = await loadGame("minecraft", { baseUrl: base, fetcher });
    expect(data.slug).toBe("minecraft");
    expect(requested).toEqual([`${base}/manifest.json`, `${base}/${version}/games/minecraft.json`]);
  });

  it("loads every artifact kind in the bundle, each valid against its schema", async () => {
    const { fetcher } = bundleFetcher();
    const options = { baseUrl: "/data", fetcher };
    expect((await loadSourceHealth(options)).data.sources.length).toBeGreaterThan(0);
    expect((await loadMethodology(options)).data.score.version).toBe(manifest().score_version);
    for (const slug of Object.keys(manifest().artifacts.games)) {
      expect((await loadGame(slug, options)).data.slug).toBe(slug);
    }
  });

  it("says a game is not in the run without fetching anything for it", async () => {
    const { fetcher, requested } = bundleFetcher();
    const error = await failure(loadGame("not-a-game", { baseUrl: "/data", fetcher }));
    expect(error.kind).toBe("not_found");
    expect(requested).toEqual(["/data/manifest.json"]);
  });

  it("does not treat an inherited property name as a game", async () => {
    const { fetcher } = bundleFetcher();
    expect((await failure(loadGame("constructor", { baseUrl: "/data", fetcher }))).kind).toBe("not_found");
    expect((await failure(loadGame("__proto__", { baseUrl: "/data", fetcher }))).kind).toBe("not_found");
  });
});

describe("refusing data that cannot be trusted", () => {
  it("reports missing data as unavailable", async () => {
    const { fetcher } = bundleFetcher("/data", { "manifest.json": null });
    const error = await failure(loadManifest({ baseUrl: "/data", fetcher }));
    expect(error.kind).toBe("unavailable");
    expect(error.url).toBe("/data/manifest.json");
  });

  it("reports a network failure as unavailable", async () => {
    const fetcher = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    expect((await failure(loadManifest({ baseUrl: "/data", fetcher }))).kind).toBe("unavailable");
  });

  it("rejects a body that is not JSON", async () => {
    const { fetcher } = bundleFetcher("/data", { "manifest.json": "<html>not found</html>" });
    expect((await failure(loadManifest({ baseUrl: "/data", fetcher }))).kind).toBe("invalid");
  });

  it("rejects a leaderboard whose score is out of range, naming where", async () => {
    const { fetcher } = bundleFetcher("/data", {
      [`${version}/current.json`]: (body) => {
        (body as { entries: { score: number }[] }).entries[0]!.score = 140;
        return body;
      },
    });
    const error = await failure(loadOverview({ baseUrl: "/data", fetcher }));
    expect(error.kind).toBe("invalid");
    expect(error.details.map((violation) => violation.path)).toContain("/entries/0/score");
  });

  it.each([
    ["a missing required field", (body: Record<string, unknown>) => void delete body.confidence_ceiling],
    ["an unknown extra field", (body: Record<string, unknown>) => void (body.event_heat = 0.9)],
    ["a wrongly typed field", (body: Record<string, unknown>) => void (body.entries = "none")],
    ["an unknown lifecycle state", (body: Record<string, unknown>) => {
      (body.entries as { lifecycle_state: string }[])[0]!.lifecycle_state = "HYPED";
    }],
    ["a malformed date", (body: Record<string, unknown>) => void (body.expected_latest_day = "03/10/2026")],
  ])("rejects a leaderboard with %s", async (_name, mutate) => {
    const { fetcher } = bundleFetcher("/data", {
      [`${version}/current.json`]: (body) => {
        mutate(body as Record<string, unknown>);
        return body;
      },
    });
    const error = await failure(loadOverview({ baseUrl: "/data", fetcher }));
    expect(error.kind).toBe("invalid");
    expect(error.details.length).toBeGreaterThan(0);
  });

  it("rejects a null where the contract requires a number, instead of reading it as zero", async () => {
    const { fetcher } = bundleFetcher("/data", {
      [`${version}/games/minecraft.json`]: (body) => {
        (body as { score: { components: { momentum: number | null } } }).score.components.momentum = null;
        return body;
      },
    });
    expect((await failure(loadGame("minecraft", { baseUrl: "/data", fetcher }))).kind).toBe("invalid");
  });

  it("rejects an artifact from a different run than the manifest selects", async () => {
    const { fetcher } = bundleFetcher("/data", {
      [`${version}/current.json`]: (body) => ({ ...(body as object), run_id: "20250101T000000Z-deadbeef" }),
    });
    const error = await failure(loadOverview({ baseUrl: "/data", fetcher }));
    expect(error.kind).toBe("inconsistent");
    expect(error.message).toContain(run);
  });

  it("rejects a contract version this interface was not generated for", async () => {
    const { fetcher } = bundleFetcher("/data", {
      "manifest.json": (body) => ({ ...(body as object), schema_version: "2.0.0" }),
    });
    expect((await failure(loadManifest({ baseUrl: "/data", fetcher }))).kind).toBe("inconsistent");
  });

  it("rejects a manifest that points outside the data root", async () => {
    const { fetcher, requested } = bundleFetcher("/data", {
      "manifest.json": (body) => {
        const changed = body as { artifacts: { current: string } };
        changed.artifacts.current = "https://evil.example.test/current.json";
        return changed;
      },
    });
    const error = await failure(loadOverview({ baseUrl: "/data", fetcher }));
    expect(error.kind).toBe("invalid");
    expect(requested.some((url) => url.includes("evil.example.test"))).toBe(false);
  });

  it("rejects a game file that describes a different game", async () => {
    const { fetcher } = bundleFetcher("/data", {
      "manifest.json": (body) => {
        const changed = body as { artifacts: { games: Record<string, string> } };
        changed.artifacts.games.minecraft = changed.artifacts.games["gta-v"]!;
        return changed;
      },
    });
    expect((await failure(loadGame("minecraft", { baseUrl: "/data", fetcher }))).kind).toBe("inconsistent");
  });
});

describe("the fixture itself", () => {
  it("is a real replay bundle: ranked by score, with unranked games kept", () => {
    const board = current();
    expect(board.entries.map((entry) => entry.rank)).toEqual(board.entries.map((_, index) => index + 1));
    expect(board.unranked.length).toBeGreaterThan(0);
  });
});
