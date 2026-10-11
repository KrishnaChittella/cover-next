import { describe, expect, it } from "vitest";

import {
  ABSENT,
  direction,
  formatConfidence,
  formatCount,
  formatDay,
  formatDecimal,
  formatScore,
  formatSignedPercent,
  formatStaleness,
  formatTimestamp,
  formatUnit,
  humanize,
  lifecycleLabel,
  unscoredLabel,
} from "./format";

describe("absent values", () => {
  it.each([formatCount, formatScore, formatConfidence, formatUnit, formatSignedPercent, formatDecimal])(
    "%o shows null and undefined as absent, never as zero",
    (format) => {
      for (const missing of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
        const shown = format(missing);
        expect(shown).toBe(ABSENT);
        expect(shown).not.toMatch(/0/);
      }
    },
  );

  it("still shows a real zero as zero", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatScore(0)).toBe("0.0");
    expect(formatConfidence(0)).toBe("0");
    expect(formatSignedPercent(0)).toBe("0.0%");
    expect(formatDay(null)).toBe(ABSENT);
    expect(formatTimestamp(undefined)).toBe(ABSENT);
  });
});

describe("scores and confidence", () => {
  it("always gives a score one decimal so a column lines up", () => {
    expect(formatScore(12.2)).toBe("12.2");
    expect(formatScore(2.3)).toBe("2.3");
    expect(formatScore(100)).toBe("100.0");
    expect(formatScore(7)).toBe("7.0");
    expect(formatScore(12.249)).toBe("12.2");
  });

  it("keeps whole confidence whole and gives the rest one decimal", () => {
    expect(formatConfidence(60)).toBe("60");
    expect(formatConfidence(41.25)).toBe("41.3");
    expect(formatConfidence(100)).toBe("100");
  });

  it("shows a component share with two decimals", () => {
    expect(formatUnit(0.1268)).toBe("0.13");
    expect(formatUnit(0)).toBe("0.00");
    expect(formatUnit(1)).toBe("1.00");
  });
});

describe("counts and changes", () => {
  it("groups thousands and drops fractions of a view", () => {
    expect(formatCount(7415.6667)).toBe("7,416");
    expect(formatCount(5520)).toBe("5,520");
    expect(formatCount(1234567)).toBe("1,234,567");
  });

  it("signs a change, with a real minus sign and no sign on zero", () => {
    expect(formatSignedPercent(34.3418)).toBe("+34.3%");
    expect(formatSignedPercent(-12.06)).toBe("−12.1%");
    expect(formatSignedPercent(0.04)).toBe("0.0%");
    expect(formatSignedPercent(-0.04)).toBe("0.0%");
    expect(formatSignedPercent(1250)).toBe("+1,250.0%");
  });

  it("names the direction of a change, and that it is unknown when absent", () => {
    expect(direction(6.77)).toBe("up");
    expect(direction(-3)).toBe("down");
    expect(direction(0.01)).toBe("flat");
    expect(direction(null)).toBe("unknown");
    expect(direction(undefined)).toBe("unknown");
  });
});

describe("dates", () => {
  it("shows a contract day as written, with no time-zone shift", () => {
    expect(formatDay("2026-10-03")).toBe("3 Oct 2026");
    expect(formatDay("2026-01-01")).toBe("1 Jan 2026");
    expect(formatDay("2026-12-31")).toBe("31 Dec 2026");
  });

  it("shows a timestamp in UTC whatever the viewer's zone", () => {
    expect(formatTimestamp("2026-10-11T02:51:33.475940Z")).toBe("11 Oct 2026, 02:51 UTC");
    expect(formatTimestamp("2026-10-11T23:30:00-05:00")).toBe("12 Oct 2026, 04:30 UTC");
  });

  it("describes staleness in words", () => {
    expect(formatStaleness(0)).toBe("current");
    expect(formatStaleness(1)).toBe("1 day behind");
    expect(formatStaleness(3)).toBe("3 days behind");
    expect(formatStaleness(null)).toBe("no observation");
  });
});

describe("labels", () => {
  it("names the contract's states and falls back readably for a new one", () => {
    expect(lifecycleLabel("LIVE_SERVICE")).toBe("Live service");
    expect(lifecycleLabel("PRE_RELEASE")).toBe("Pre-release");
    expect(lifecycleLabel("EARLY_ACCESS")).toBe("Early access");
    expect(unscoredLabel("no_observations")).toBe("No observations");
    expect(unscoredLabel("zero_dispersion")).toBe("No variation in the baseline");
    expect(humanize("wikimedia_pageviews")).toBe("Wikimedia pageviews");
  });
});
