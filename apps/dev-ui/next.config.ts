import path from "node:path";
import type { NextConfig } from "next";

// A private, local interface. It reads static artifacts over HTTP and has no server
// of its own beyond what Next needs to serve pages.
const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  turbopack: { root: path.resolve(import.meta.dirname) },
};

export default config;
