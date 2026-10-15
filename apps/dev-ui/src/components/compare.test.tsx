import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  briefText,
  buildBrief,
  PATH_WITHHELD,
  REFERENCE_WITHHELD,
  safeReference,
  safeText,
  SYNTHETIC_WARNING,
} from "@/private/opportunities/brief";
import { buildComparison, comparisonText, NO_GENUINE_OPPORTUNITY, parseSelection } from "@/private/opportunities/compare";
import { enablement } from "@/private/opportunities/loader";
import type { OpportunityAssessment, OpportunityReport } from "@/private/opportunities/types";
import { checkReport } from "@/private/opportunities/validate";
import recordedJson from "@/test/fixtures/opportunities/recorded.json";
import syntheticJson from "@/test/fixtures/opportunities/synthetic.json";

import { CompareSelector, OpportunityBoard, OpportunityComparison } from "./opportunities";

const recorded = recordedJson as unknown as OpportunityReport;
const synthetic = syntheticJson as unknown as OpportunityReport;

function variant(report: OpportunityReport, slug: string, change: (a: OpportunityAssessment) => void) {
  const copy = structuredClone(report);
  change(copy.assessments.find((a) => a.slug === slug)!);
  return copy;
}

function shown(report: OpportunityReport, slugs: string[], view: "verified" | "synthetic" = "verified") {
  const comparison = buildComparison(report, slugs, view);
  render(<OpportunityComparison comparison={comparison} view={view} />);
  return comparison;
}

describe("choosing games", () => {
  it("accepts two or three games from the report, and refuses anything else plainly", () => {
    expect(parseSelection(["minecraft", "gta-v"], recorded)).toEqual({ kind: "ok", slugs: ["minecraft", "gta-v"] });
    expect(parseSelection("minecraft,gta-v,rust", recorded)).toEqual({
      kind: "ok",
      slugs: ["minecraft", "gta-v", "rust"],
    });
    expect(parseSelection(["minecraft", "not-a-game"], recorded)).toEqual({
      kind: "invalid",
      message: "“not-a-game” is not a game in this report.",
    });
    expect(parseSelection(["minecraft", "minecraft"], recorded)).toEqual({
      kind: "invalid",
      message: "“minecraft” is chosen more than once.",
    });
    expect(parseSelection(["minecraft", "gta-v", "rust", "dota-2"], recorded).kind).toBe("invalid");
    expect(parseSelection("a,b,c,d", recorded)).toMatchObject({ message: expect.stringContaining("at most 3") });
    expect(parseSelection("minecraft", recorded)).toMatchObject({ message: "Choose 2 or 3 games to compare." });
    expect(parseSelection(undefined, recorded).kind).toBe("invalid");
    // Paths and markup are never echoed back as if they were game names.
    const hostile = parseSelection(["minecraft", "../../etc/passwd"], recorded);
    expect(hostile).toEqual({ kind: "invalid", message: "A selection is not a game in this report." });
    expect(parseSelection(["minecraft", "<b>x</b>"], recorded)).toMatchObject({ message: "A selection is not a game in this report." });
  });

  it("offers a plain form of the report's own games, linked from the shortlist", () => {
    render(<OpportunityBoard report={recorded} view="verified" />);
    const form = screen.getByTestId("compare-selector");
    expect(form).toHaveAttribute("action", "/opportunities/compare");
    expect(form).toHaveAttribute("method", "get");
    const boxes = within(form).getAllByRole("checkbox");
    expect(boxes.map((b) => (b as HTMLInputElement).value)).toEqual(recorded.entries.map((e) => e.slug));
    expect(within(form).queryByDisplayValue("synthetic")).toBeNull();
    render(<CompareSelector report={synthetic} view="synthetic" />);
    expect(screen.getAllByTestId("compare-selector")[1]!.querySelector('input[name="view"]')).toHaveValue("synthetic");
  });
});

describe("the comparison keeps the engine's order, readiness and ties", () => {
  it("compares two games in engine order, whatever order they were chosen in", () => {
    const c = shown(recorded, ["minecraft", "rust"]);
    expect(c.columns.map((col) => col.entry.slug)).toEqual(["rust", "minecraft"]);
    expect(c.columns.map((col) => col.entry.position)).toEqual([1, 3]);
    const headers = screen.getAllByTestId("comparison-column").map((h) => h.textContent);
    expect(headers).toEqual(["1. Rust", "3. Minecraft"]);
  });

  it("compares three games and says a tie is a tie", () => {
    const c = shown(recorded, ["gta-v", "minecraft", "rust"]);
    expect(c.columns).toHaveLength(3);
    // The engine's entries 1 (Rust) and 2 (GTA V) are "indistinguishable on the evidence".
    expect(c.ties).toEqual([["Rust", "Grand Theft Auto V"]]);
    expect(screen.getByTestId("comparison-tie")).toHaveTextContent("ordered by id only, which means nothing");
    expect(comparisonText(c)).toContain("Tied in the engine's order (ordered by id only, which means nothing)");
  });

  it("creates no score and no winner", () => {
    const c = shown(recorded, ["gta-v", "minecraft", "rust"]);
    for (const col of c.columns) {
      expect(col.brief.readiness).toBe(recorded.assessments.find((a) => a.slug === col.entry.slug)!.readiness);
      expect(Object.keys(col).some((k) => /score|rank|winner/i.test(k))).toBe(false);
    }
    const text = comparisonText(c);
    expect(text).not.toMatch(/\b(winner|best choice|we recommend|top pick)\b/i);
    expect(text).toContain("A higher position is not a predicted video outcome");
  });
});

describe("abstention, watchlists and candidates", () => {
  it("abstains plainly when every selected game is insufficient", () => {
    const c = shown(recorded, ["minecraft", "gta-v"]);
    expect(c.noGenuineOpportunity).toBe(true);
    expect(screen.getByTestId("comparison-abstains")).toHaveTextContent(NO_GENUINE_OPPORTUNITY);
    expect(comparisonText(c)).toContain(NO_GENUINE_OPPORTUNITY);
    expect(screen.getByTestId("comparison-questions").textContent).toContain("That audience attention is actually rising");
  });

  it("calls a watchlist a watchlist and a verified candidate what the rubric supports", () => {
    const report = variant(
      variant(synthetic, "dota-2", (a) => {
        a.readiness = "evidence_supported_candidate";
        a.facts = structuredClone(a.simulated_facts);
      }),
      "rust",
      (a) => {
        a.readiness = "provisional_watchlist";
        a.facts = structuredClone(a.simulated_facts);
      },
    );
    const c = buildComparison(report, ["dota-2", "rust"], "verified");
    expect(c.noGenuineOpportunity).toBe(false);
    const text = comparisonText(c);
    expect(text).toContain("The rubric supports further creator consideration of this game. It is not a guarantee");
    expect(text).toContain("Watchlist — not a recommendation.");
    expect(text).not.toContain(NO_GENUINE_OPPORTUNITY);
    expect(text).not.toContain(SYNTHETIC_WARNING);
  });
});

describe("missing evidence and provenance", () => {
  it("keeps missing creator and community coverage unknown, never low", () => {
    const text = comparisonText(buildComparison(recorded, ["minecraft", "gta-v"], "verified"));
    expect(text).toContain("That is not low competition: competition is simply unknown.");
    expect(text).toContain("That is not low interest: interest is simply unknown.");
    expect(text.replace(/not low competition|not evidence of low competition/g, "")).not.toMatch(/low competition/i);
  });

  it("an empty creator sample is still a lower bound", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.creator_coverage = "[VERIFIED] Sample complete: the search returned no videos for this game.";
      a.facts = { ...a.facts, coverage_known: true };
    });
    const text = comparisonText(buildComparison(report, ["minecraft", "gta-v"], "verified"));
    expect(text).toContain("Even a complete sample is a lower bound: total creator competition is unknown.");
  });

  it("shows unverified events as leads with their provenance", () => {
    const withEvent = synthetic.assessments.find((a) => a.events.length > 0)!;
    const other = synthetic.assessments.find((a) => a.slug !== withEvent.slug)!;
    const c = shown(synthetic, [withEvent.slug, other.slug]);
    const text = comparisonText(c);
    expect(text).toMatch(/\[SYNTHETIC\] .*starting .* — lead, not counted/);
    expect(screen.getAllByText("lead, not counted").length).toBeGreaterThan(0);
  });

  it("shows contradictory demand as counterevidence", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.movements = [
        { ...a.movements[0]!, direction: "up", standing: "verified" },
        { ...a.movements[0]!, family: "steam_concurrent_players", direction: "down", standing: "verified" },
      ];
      a.facts = { ...a.facts, moving_up: ["wikimedia_readership"], moving_down: ["steam_concurrent_players"], disagreement: true };
    });
    const text = comparisonText(buildComparison(report, ["minecraft", "gta-v"], "verified"));
    expect(text).toContain("steam concurrent players is DOWN against its own history");
    expect(text).toContain("Demand families disagree");
  });

  it("keeps the as-of moment, freshness and sources", () => {
    const text = comparisonText(buildComparison(recorded, ["minecraft", "gta-v"], "verified"));
    expect(text).toContain("Assessed as of 2026-10-05 12:00 UTC with creator_opportunity_rules_v1.");
    expect(text).toMatch(/wikimedia_pageviews: available; latest .* UTC; VERIFIED/);
    expect(text).toMatch(/ {2}Sources:\n {4}igdb/);
  });
});

describe("copying and synthetic separation", () => {
  it("copies exactly what the plain-text view shows", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    shown(recorded, ["minecraft", "gta-v"]);
    fireEvent.click(screen.getByRole("button", { name: "Copy comparison as text" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("comparison-text").textContent).toBe(writeText.mock.calls[0]![0]);
  });

  it("a synthetic comparison is marked on screen, and first and last in its text", () => {
    const c = shown(synthetic, ["dota-2", "rust"], "synthetic");
    expect(screen.getByTestId("comparison-synthetic")).toHaveTextContent(SYNTHETIC_WARNING);
    expect(screen.getByTestId("comparison")).toHaveAttribute("data-basis", "simulated");
    const lines = comparisonText(c).trim().split("\n");
    expect(lines[0]).toBe(`*** ${SYNTHETIC_WARNING} ***`);
    expect(lines.at(-1)).toBe(`*** ${SYNTHETIC_WARNING} ***`);
    const text = lines.join("\n");
    expect(text).toContain("Readiness: SIMULATED Evidence-supported candidate (verified readiness: Insufficient evidence)");
    expect(text).toContain("Readiness: SIMULATED Watchlist (provisional) (verified readiness: Insufficient evidence)");
    // The verified claim is still made about the verified evidence, and only about it.
    expect(text).toContain(NO_GENUINE_OPPORTUNITY);
  });

  it("the verified view of a synthetic report counts nothing invented", () => {
    const c = buildComparison(synthetic, ["dota-2", "rust"], "verified");
    expect(c.synthetic).toBe(false);
    expect(c.columns.every((col) => col.brief.whyNow.length === 0)).toBe(true);
    expect(comparisonText(c)).not.toContain("SIMULATED");
  });
});

describe("path hygiene at the copy boundary", () => {
  const leaks = [
    "/Users/someone/.metaspike/private/opportunities/verified.json",
    "/home/dev/work/report.json",
    "C:\\Users\\someone\\report.json",
    "\\\\server\\share\\report.json",
    "~/MetaSpike/.metaspike/private/x.json",
    ".metaspike/private/opportunities/synthetic.json",
    "./tests/fixtures/wikimedia",
    "../MetaSpike-product-ui/apps",
    "apps/dev-ui/src/private",
    "/workspace/metaspike/x",
  ];

  it("withholds POSIX, Windows, home, relative and workspace paths from free text", () => {
    for (const leak of leaks) {
      const out = safeText(`read from ${leak}, then summarised`);
      expect(out, leak).toBe(`read from ${PATH_WITHHELD} then summarised`);
    }
  });

  it("keeps web links whole, whatever words their path holds, and keeps plain prose", () => {
    const urls = [
      "https://store.steampowered.com/news/app/570/view/88001",
      "https://www.reddit.com/r/private/comments/abc/",
      "https://example.org/Users/home/private/tmp",
      "http://wikimedia.org/api/rest_v1/metrics/pageviews",
    ];
    for (const url of urls) {
      expect(safeText(`see ${url}.`)).toBe(`see ${url}.`);
      expect(safeReference(url)).toBe(url);
    }
    for (const prose of ["the season (upcoming) / patch", "34/34 positive", "1 verified source(s) answered"]) {
      expect(safeText(prose)).toBe(prose);
    }
    for (const id of ["wikimedia_pageviews", "evt_3cae87b2b58512487af3afda", "t3_abc123", "youtube:video#1"]) {
      expect(safeReference(id)).toBe(id);
    }
    expect(safeReference("/Users/someone/x")).toBe(REFERENCE_WITHHELD);
    expect(safeReference("file:///etc/passwd")).toBe(REFERENCE_WITHHELD);
    expect(safeReference("javascript:alert(1)")).toBe(REFERENCE_WITHHELD);
  });

  it("no copied brief or comparison carries a path planted anywhere in the report", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.name = "Minecraft /Users/a/x";
      a.creator_coverage = `No creator-supply input: ${leaks[0]}`;
      a.community = `No community input (${leaks[2]})`;
      a.missing = [...a.missing, `see ${leaks[5]}`];
      a.caveats = [`from ${leaks[4]}`];
      a.uncertainty = `computed in ${leaks[9]}`;
      a.why_now = [{ text: `up, per ${leaks[1]}`, standing: "verified", references: [leaks[0]!, "wikimedia_pageviews"] }];
    });
    const brief = briefText(buildBrief(report, report.assessments.find((a) => a.slug === "minecraft")!, "verified"));
    const comparison = comparisonText(buildComparison(report, ["minecraft", "gta-v"], "verified"));
    for (const text of [brief, comparison]) {
      for (const fragment of ["/Users/", "/home/", "C:\\", "\\\\server", "~/", ".metaspike/", "/workspace/"]) {
        expect(text, fragment).not.toContain(fragment);
      }
      expect(text).toContain(PATH_WITHHELD);
      expect(text).toContain("wikimedia_pageviews");
    }
  });
});

describe("the existing private boundary holds", () => {
  it("is disabled in production and without the flag, and refuses an invalid report", () => {
    expect(enablement({ NODE_ENV: "production", METASPIKE_PRIVATE_OPPORTUNITIES: "1" }).enabled).toBe(false);
    expect(enablement({ NODE_ENV: "development" }).enabled).toBe(false);
    const broken = structuredClone(recordedJson);
    broken.assessments.push(structuredClone(broken.assessments[0]!));
    expect(checkReport(broken).kind).toBe("invalid");
  });
});
