import path from "node:path";
import type { NextConfig } from "next";

// Static files only, served by GitHub Pages at cover-next.krishnachittella.dev.
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  turbopack: { root: path.resolve(import.meta.dirname) },
};

export default config;
