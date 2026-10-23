import path from "node:path";
import type { NextConfig } from "next";

// Static files only, served by GitHub Pages under krishnachittella.dev/cover-next.
const config: NextConfig = {
  output: "export",
  basePath: "/cover-next",
  trailingSlash: true,
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  turbopack: { root: path.resolve(import.meta.dirname) },
};

export default config;
