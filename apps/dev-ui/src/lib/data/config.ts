// One base URL decides where artifacts come from. The UI does not know or care whether
// it is the local bundle under /data or a CDN: everything below it follows the
// manifest's own paths.
export const DEFAULT_DATA_BASE = "/data";
export const MANIFEST_FILE = "manifest.json";

/** The configured base URL, without a trailing slash. Empty or unset means the default. */
export function dataBaseUrl(raw: string | undefined = process.env.NEXT_PUBLIC_METASPIKE_DATA_URL): string {
  const value = (raw ?? "").trim();
  if (value === "") return DEFAULT_DATA_BASE;
  const trimmed = value.replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

export class UnsafePathError extends Error {}

/**
 * A manifest path joined to the base. Manifest paths are relative to the artifact root
 * and stay inside it: an absolute URL, a leading slash, a backslash or a `..` segment
 * is refused, so a manifest can never point the UI somewhere else.
 */
export function resolveDataUrl(base: string, relative: string): string {
  if (
    relative === "" ||
    relative.startsWith("/") ||
    relative.includes("\\") ||
    relative.includes("?") ||
    relative.includes("#") ||
    /^[a-z][a-z0-9+.-]*:/i.test(relative) ||
    relative.split("/").some((part) => part === ".." || part === "." || part === "")
  ) {
    throw new UnsafePathError(`refusing artifact path outside the data root: ${JSON.stringify(relative)}`);
  }
  return base === "/" ? `/${relative}` : `${base}/${relative}`;
}
