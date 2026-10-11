// Reads artifacts the way any consumer must: the manifest first, then exactly the files
// it names, each checked against its schema and against the manifest's run. Nothing is
// flattened or re-shaped; the manifest and version-path model is kept as published.
import type {
  ArtifactKind,
  CurrentArtifact,
  GameArtifact,
  Manifest,
  MethodologyArtifact,
  SourceHealthArtifact,
} from "@/contract";
import { SUPPORTED_SCHEMA_VERSION, violations, type Violation } from "@/contract/validate";

import { MANIFEST_FILE, UnsafePathError, dataBaseUrl, resolveDataUrl } from "./config";

export type DataErrorKind =
  /** The file could not be fetched at all: nothing synced, wrong base URL, offline. */
  | "unavailable"
  /** The file is not JSON, or breaks its schema. */
  | "invalid"
  /** Files that should belong together do not: different runs, unknown contract. */
  | "inconsistent"
  /** The manifest does not list what was asked for. */
  | "not_found";

export class DataError extends Error {
  constructor(
    readonly kind: DataErrorKind,
    message: string,
    readonly url: string | null = null,
    readonly details: readonly Violation[] = [],
  ) {
    super(message);
    this.name = "DataError";
  }
}

export interface LoadOptions {
  baseUrl?: string;
  fetcher?: typeof fetch;
  signal?: AbortSignal;
}

export interface Loaded<T> {
  manifest: Manifest;
  data: T;
}

async function fetchJson(url: string, options: LoadOptions): Promise<unknown> {
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try {
    // Artifacts are replaced by publishing a new manifest; never serve a stale one.
    response = await fetcher(url, { cache: "no-store", signal: options.signal });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new DataError("unavailable", `Could not reach ${url}.`, url);
  }
  if (!response.ok) {
    throw new DataError("unavailable", `${url} answered ${response.status}.`, url);
  }
  try {
    return (await response.json()) as unknown;
  } catch {
    throw new DataError("invalid", `${url} is not valid JSON.`, url);
  }
}

function conform<T>(kind: ArtifactKind, value: unknown, url: string): T {
  const found = violations(kind, value);
  if (found.length > 0) {
    throw new DataError(
      "invalid",
      `${url} does not match the ${kind} schema (${found.length} violation${found.length === 1 ? "" : "s"}).`,
      url,
      found,
    );
  }
  const version = (value as { schema_version: string }).schema_version;
  if (version !== SUPPORTED_SCHEMA_VERSION) {
    throw new DataError(
      "inconsistent",
      `${url} is contract ${version}; this interface was generated for ${SUPPORTED_SCHEMA_VERSION}.`,
      url,
    );
  }
  return value as T;
}

function urlFor(base: string, relative: string): string {
  try {
    return resolveDataUrl(base, relative);
  } catch (cause) {
    if (cause instanceof UnsafePathError) throw new DataError("invalid", cause.message, null);
    throw cause;
  }
}

export async function loadManifest(options: LoadOptions = {}): Promise<Manifest> {
  const base = options.baseUrl ?? dataBaseUrl();
  const url = urlFor(base, MANIFEST_FILE);
  return conform<Manifest>("manifest", await fetchJson(url, options), url);
}

async function loadFrom<T extends { run_id: string }>(
  manifest: Manifest,
  kind: ArtifactKind,
  relative: string,
  options: LoadOptions,
): Promise<T> {
  const base = options.baseUrl ?? dataBaseUrl();
  const url = urlFor(base, relative);
  const artifact = conform<T>(kind, await fetchJson(url, options), url);
  if (artifact.run_id !== manifest.run_id) {
    throw new DataError(
      "inconsistent",
      `${url} is from run ${artifact.run_id}, but the manifest selects ${manifest.run_id}.`,
      url,
    );
  }
  return artifact;
}

export interface Overview {
  current: CurrentArtifact;
  sourceHealth: SourceHealthArtifact;
}

export async function loadOverview(options: LoadOptions = {}): Promise<Loaded<Overview>> {
  const manifest = await loadManifest(options);
  const [current, sourceHealth] = await Promise.all([
    loadFrom<CurrentArtifact>(manifest, "current", manifest.artifacts.current, options),
    loadFrom<SourceHealthArtifact>(manifest, "source-health", manifest.artifacts.source_health, options),
  ]);
  return { manifest, data: { current, sourceHealth } };
}

export async function loadCurrent(options: LoadOptions = {}): Promise<Loaded<CurrentArtifact>> {
  const manifest = await loadManifest(options);
  const data = await loadFrom<CurrentArtifact>(manifest, "current", manifest.artifacts.current, options);
  return { manifest, data };
}

export async function loadGame(slug: string, options: LoadOptions = {}): Promise<Loaded<GameArtifact>> {
  const manifest = await loadManifest(options);
  const relative = Object.hasOwn(manifest.artifacts.games, slug) ? manifest.artifacts.games[slug] : undefined;
  if (relative === undefined) {
    throw new DataError("not_found", `The current run has no game with the slug "${slug}".`);
  }
  const data = await loadFrom<GameArtifact>(manifest, "game", relative, options);
  if (data.slug !== slug) {
    throw new DataError("inconsistent", `${relative} describes "${data.slug}", not "${slug}".`);
  }
  return { manifest, data };
}

export async function loadSourceHealth(options: LoadOptions = {}): Promise<Loaded<SourceHealthArtifact>> {
  const manifest = await loadManifest(options);
  const data = await loadFrom<SourceHealthArtifact>(
    manifest,
    "source-health",
    manifest.artifacts.source_health,
    options,
  );
  return { manifest, data };
}

export async function loadMethodology(options: LoadOptions = {}): Promise<Loaded<MethodologyArtifact>> {
  const manifest = await loadManifest(options);
  const data = await loadFrom<MethodologyArtifact>(
    manifest,
    "methodology",
    manifest.artifacts.methodology,
    options,
  );
  return { manifest, data };
}
