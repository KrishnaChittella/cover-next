import { describe, expect, it } from "vitest";

import { DEFAULT_DATA_BASE, UnsafePathError, dataBaseUrl, resolveDataUrl } from "./config";

describe("the data base URL", () => {
  it("defaults to the local bundle when unset, empty or blank", () => {
    expect(DEFAULT_DATA_BASE).toBe("/data");
    expect(dataBaseUrl(undefined)).toBe("/data");
    expect(dataBaseUrl("")).toBe("/data");
    expect(dataBaseUrl("   ")).toBe("/data");
  });

  it("accepts a local path or a remote origin and drops trailing slashes", () => {
    expect(dataBaseUrl("/data/")).toBe("/data");
    expect(dataBaseUrl("https://cdn.example.test/metaspike///")).toBe("https://cdn.example.test/metaspike");
    expect(dataBaseUrl(" https://cdn.example.test ")).toBe("https://cdn.example.test");
    expect(dataBaseUrl("/")).toBe("/");
  });

  it("reads NEXT_PUBLIC_METASPIKE_DATA_URL by default", () => {
    const before = process.env.NEXT_PUBLIC_METASPIKE_DATA_URL;
    process.env.NEXT_PUBLIC_METASPIKE_DATA_URL = "https://r2.example.test/bundle/";
    try {
      expect(dataBaseUrl()).toBe("https://r2.example.test/bundle");
    } finally {
      if (before === undefined) delete process.env.NEXT_PUBLIC_METASPIKE_DATA_URL;
      else process.env.NEXT_PUBLIC_METASPIKE_DATA_URL = before;
    }
  });
});

describe("resolving a manifest path", () => {
  it("joins a relative path to any base, keeping the version path", () => {
    expect(resolveDataUrl("/data", "manifest.json")).toBe("/data/manifest.json");
    expect(resolveDataUrl("/data", "versions/20261011T025133Z-a8861c54/games/minecraft.json")).toBe(
      "/data/versions/20261011T025133Z-a8861c54/games/minecraft.json",
    );
    expect(resolveDataUrl("https://cdn.example.test/ms", "versions/r1/current.json")).toBe(
      "https://cdn.example.test/ms/versions/r1/current.json",
    );
    expect(resolveDataUrl("/", "manifest.json")).toBe("/manifest.json");
  });

  it.each([
    "",
    "/etc/passwd",
    "../secrets.json",
    "versions/../../x.json",
    "versions/./x.json",
    "versions//x.json",
    "https://evil.example.test/current.json",
    "//evil.example.test/current.json",
    "javascript:alert(1)",
    "versions\\x.json",
    "current.json?redirect=1",
    "current.json#frag",
  ])("refuses a path that leaves the data root: %j", (relative) => {
    expect(() => resolveDataUrl("/data", relative)).toThrow(UnsafePathError);
  });
});
