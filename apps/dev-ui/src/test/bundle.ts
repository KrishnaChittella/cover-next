// The fixture bundle, served to the data client the way a static host would serve it.
import { readFileSync } from "node:fs";
import path from "node:path";

import type { CurrentArtifact, GameArtifact, Manifest, MethodologyArtifact, SourceHealthArtifact } from "@/contract";

const ROOT = path.resolve(import.meta.dirname, "fixtures", "bundle");

export function readBundleFile<T>(relative: string): T {
  return JSON.parse(readFileSync(path.join(ROOT, relative), "utf8")) as T;
}

export const manifest = (): Manifest => readBundleFile<Manifest>("manifest.json");
export const current = (): CurrentArtifact => readBundleFile<CurrentArtifact>(manifest().artifacts.current);
export const sourceHealth = (): SourceHealthArtifact =>
  readBundleFile<SourceHealthArtifact>(manifest().artifacts.source_health);
export const methodology = (): MethodologyArtifact =>
  readBundleFile<MethodologyArtifact>(manifest().artifacts.methodology);
export const game = (slug: string): GameArtifact => {
  const relative = manifest().artifacts.games[slug];
  if (!relative) throw new Error(`fixture has no game ${slug}`);
  return readBundleFile<GameArtifact>(relative);
};

export type Transform = (value: unknown) => unknown;
export type Overrides = Record<string, string | null | Transform>;

/**
 * A `fetch` that answers from the fixture bundle under `base`. An override transforms the
 * body at a path relative to the base, or replaces it with raw text; `null` makes it a 404.
 */
export function bundleFetcher(base = "/data", overrides: Overrides = {}) {
  const requested: string[] = [];
  const fetcher = (async (input: RequestInfo | URL) => {
    const url = String(input);
    requested.push(url);
    const prefix = base === "/" ? "/" : `${base}/`;
    if (!url.startsWith(prefix)) return new Response("outside the data root", { status: 404 });
    const relative = url.slice(prefix.length);
    let body: unknown;
    try {
      body = readBundleFile<unknown>(relative);
    } catch {
      return new Response("not found", { status: 404 });
    }
    if (Object.hasOwn(overrides, relative)) {
      const override = overrides[relative];
      if (override === null) return new Response("not found", { status: 404 });
      body = typeof override === "function" ? override(structuredClone(body)) : override;
    }
    return new Response(typeof body === "string" ? body : JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return { fetcher, requested };
}
