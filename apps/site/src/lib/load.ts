import { readFileSync } from "node:fs";
import path from "node:path";

import type { SiteData } from "./site";

/** Read at build time. The build fails loudly when the file is missing. */
export function loadSiteData(): SiteData {
  const file = path.join(process.cwd(), "data", "site.json");
  return JSON.parse(readFileSync(file, "utf-8")) as SiteData;
}
