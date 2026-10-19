import { describe, expect, it } from "vitest";

import type { HistorySeries } from "@/contract";
import { game } from "@/test/bundle";

import { chartSeries, densify, summarise } from "./history";

function series(points: HistorySeries["points"]): HistorySeries {
  return { source: "wikimedia_pageviews", metric: "pageviews", granularity: "daily", attribution: null, points };
}

const withGaps = series([
  { day: "2026-09-01", value: 100, rolling_mean_7d: null },
  { day: "2026-09-02", value: null, rolling_mean_7d: null }, // carried as null
  { day: "2026-09-03", value: 120, rolling_mean_7d: 110 },
  // 2026-09-04 and 2026-09-05 are left out of the series altogether
  { day: "2026-09-06", value: 90, rolling_mean_7d: 103.33 },
]);

describe("missing days", () => {
  it("keeps a null day as null and turns an omitted day into null", () => {
    const dense = densify(withGaps);
    expect(dense.days).toEqual(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06"]);
    expect(dense.values.map(([, value]) => value)).toEqual([100, null, 120, null, null, 90]);
    expect(dense.rolling.map(([, value]) => value)).toEqual([null, null, 110, null, null, 103.33]);
  });

  it("never draws a missing day as zero and never joins across it", () => {
    const [raw, rolling] = chartSeries(withGaps);
    expect(raw!.connectNulls).toBe(false);
    expect(rolling!.connectNulls).toBe(false);
    expect(raw!.data.some(([, value]) => value === 0)).toBe(false);
    expect(raw!.data.filter(([, value]) => value === null).map(([day]) => day)).toEqual([
      "2026-09-02",
      "2026-09-04",
      "2026-09-05",
    ]);
  });

  it("does not invent a rolling mean where the artifact has none", () => {
    const [, rolling] = chartSeries(withGaps);
    expect(rolling!.data[0]).toEqual(["2026-09-01", null]);
  });

  it("orders points by day even if the artifact did not", () => {
    const shuffled = series([...withGaps.points].reverse());
    expect(densify(shuffled).days).toEqual(densify(withGaps).days);
  });

  it("crosses month and year ends one calendar day at a time", () => {
    const dense = densify(
      series([
        { day: "2026-12-30", value: 1 },
        { day: "2027-01-02", value: 2 },
      ]),
    );
    expect(dense.days).toEqual(["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"]);
  });
});

describe("the text summary", () => {
  it("counts observed and missing days and finds the extremes among observed values", () => {
    expect(summarise(withGaps)).toEqual({
      firstDay: "2026-09-01",
      lastDay: "2026-09-06",
      calendarDays: 6,
      observedDays: 3,
      missingDays: 3,
      latest: { day: "2026-09-06", value: 90 },
      peak: { day: "2026-09-03", value: 120 },
      low: { day: "2026-09-06", value: 90 },
    });
  });

  it("treats an observed zero as a value, not as missing", () => {
    const summary = summarise(series([{ day: "2026-09-01", value: 0 }, { day: "2026-09-02", value: 5 }]));
    expect(summary.observedDays).toBe(2);
    expect(summary.missingDays).toBe(0);
    expect(summary.low).toEqual({ day: "2026-09-01", value: 0 });
  });

  it("handles a series with no points, and one with nothing observed", () => {
    expect(summarise(series([]))).toMatchObject({ calendarDays: 0, observedDays: 0, latest: null, peak: null });
    expect(densify(series([]))).toEqual({ days: [], values: [], rolling: [] });
    const blank = summarise(series([{ day: "2026-09-01", value: null }, { day: "2026-09-02" }]));
    expect(blank).toMatchObject({ calendarDays: 2, observedDays: 0, missingDays: 2, latest: null });
  });
});

describe("the real replay history", () => {
  it("is passed through unchanged: same days, same values, same means", () => {
    const published = game("minecraft").history[0]!;
    const dense = densify(published);
    expect(dense.days).toEqual(published.points.map((point) => point.day));
    expect(dense.values.map(([, value]) => value)).toEqual(published.points.map((point) => point.value ?? null));
    expect(dense.rolling.map(([, value]) => value)).toEqual(
      published.points.map((point) => point.rolling_mean_7d ?? null),
    );
    const summary = summarise(published);
    expect(summary.latest).toEqual({ day: "2026-10-03", value: 6013 });
    expect(summary.observedDays).toBe(90);
  });

  it("has no series at all for a game the run could not observe", () => {
    expect(game("fivem").history).toEqual([]);
  });
});
