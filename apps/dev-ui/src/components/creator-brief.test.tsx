import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildBrief, briefText, SYNTHETIC_WARNING } from "@/private/opportunities/brief";
import { enablement } from "@/private/opportunities/loader";
import type { OpportunityAssessment, OpportunityReport } from "@/private/opportunities/types";
import { checkReport } from "@/private/opportunities/validate";
import recordedJson from "@/test/fixtures/opportunities/recorded.json";
import syntheticJson from "@/test/fixtures/opportunities/synthetic.json";

import { CreatorBrief, NO_REAL_OPPORTUNITY, OpportunityDetail } from "./opportunities";

const recorded = recordedJson as unknown as OpportunityReport;
const synthetic = syntheticJson as unknown as OpportunityReport;

function game(report: OpportunityReport, slug: string): OpportunityAssessment {
  return report.assessments.find((a) => a.slug === slug)!;
}

/** A copy of one assessment, changed as a test needs. The engine's output is never edited in place. */
function variant(report: OpportunityReport, slug: string, change: (a: OpportunityAssessment) => void) {
  const copy = structuredClone(report);
  change(copy.assessments.find((a) => a.slug === slug)!);
  return copy;
}

describe("the decision brief follows the engine's readiness", () => {
  it("a verified candidate may be considered further, with its limits", () => {
    // Shaped as the engine would emit a verified candidate: real readiness and facts.
    const report = variant(synthetic, "dota-2", (a) => {
      a.readiness = "evidence_supported_candidate";
      a.facts = structuredClone(a.simulated_facts);
    });
    const brief = buildBrief(report, game(report, "dota-2"), "verified");
    expect(brief.basis).toBe("verified");
    expect(brief.decision).toContain("supports further creator consideration");
    expect(brief.decision).toContain("not a guarantee");
    expect(brief.decisionDetail.join(" ")).toContain("thresholds are unvalidated");
    expect(brief.conditions.every((c) => c.met)).toBe(true);
    expect(briefText(brief)).not.toContain(SYNTHETIC_WARNING);
  });

  it("a watchlist entry is framed as a watchlist, not a recommendation", () => {
    const report = variant(synthetic, "rust", (a) => {
      a.readiness = "provisional_watchlist";
      a.facts = structuredClone(a.simulated_facts);
    });
    const brief = buildBrief(report, game(report, "rust"), "verified");
    expect(brief.decision).toMatch(/^Watchlist — not a recommendation/);
    expect(brief.decisionDetail.join(" ")).toMatch(/Not met: .*/);
    expect(brief.decisionDetail.join(" ")).toContain("not to publish");
  });

  it("insufficient evidence abstains, and says why without treating gaps as negatives", () => {
    const brief = buildBrief(recorded, game(recorded, "minecraft"), "verified");
    expect(brief.readiness).toBe("insufficient_evidence");
    expect(brief.decision).toBe('No recommendation. MetaSpike abstains: nothing counted says "now" for this game.');
    expect(brief.decisionDetail.join(" ")).toContain("Missing evidence is not negative evidence");
  });

  it("the recorded report establishes no candidate, in every brief and on the page", () => {
    for (const a of recorded.assessments) {
      const brief = buildBrief(recorded, a, "verified");
      expect(brief.readiness).toBe(a.readiness);
      expect(brief.readiness).toBe("insufficient_evidence");
      expect(briefText(brief)).toContain("No recommendation.");
    }
    render(<OpportunityDetail report={recorded} slug="minecraft" view="verified" />);
    expect(screen.getByTestId("no-real-opportunity")).toHaveTextContent(NO_REAL_OPPORTUNITY);
    expect(screen.getByTestId("creator-brief")).toHaveAttribute("data-basis", "verified");
  });

  it("uses the engine's own position and computes no score", () => {
    for (const [report, view, entries] of [
      [recorded, "verified", recorded.entries],
      [synthetic, "synthetic", synthetic.simulated_entries],
    ] as const) {
      for (const entry of entries) {
        const a = report.assessments.find((x) => x.canonical_game_id === entry.canonical_game_id)!;
        const brief = buildBrief(report, a, view);
        expect(brief.position).toBe(entry.position);
        expect(brief.readiness).toBe(view === "synthetic" ? a.simulated_readiness : a.readiness);
        // No score field, and any line about a score is the engine's own words, verbatim.
        expect(Object.keys(brief).some((key) => /score/i.test(key))).toBe(false);
        const engine = [...a.missing, ...a.caveats, a.uncertainty];
        for (const line of briefText(brief).split("\n").filter((l) => /score|\/100|\d+%/i.test(l))) {
          expect(engine.some((said) => line.includes(said))).toBe(true);
        }
      }
    }
  });
});

describe("synthetic briefs are unmistakable", () => {
  it("a simulated candidate carries the warning on screen and is never a real recommendation", () => {
    render(<OpportunityDetail report={synthetic} slug="dota-2" view="synthetic" />);
    const brief = screen.getByTestId("creator-brief");
    expect(brief).toHaveAttribute("data-basis", "simulated");
    expect(within(brief).getByTestId("brief-synthetic")).toHaveTextContent(SYNTHETIC_WARNING);
    expect(within(brief).getByTestId("brief-decision").textContent).toMatch(/^SIMULATED — The rubric supports/);
    expect(brief).toHaveTextContent("verified readiness: Insufficient evidence");
  });

  it("the copied text opens and closes with the warning, and labels every invented item", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CreatorBrief brief={buildBrief(synthetic, game(synthetic, "dota-2"), "synthetic")} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy brief as text" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = String(writeText.mock.calls[0]![0]);
    const lines = copied.trim().split("\n");
    expect(lines[0]).toBe(`*** ${SYNTHETIC_WARNING} ***`);
    expect(lines.at(-1)).toBe(`*** ${SYNTHETIC_WARNING} ***`);
    expect(copied).toContain("Readiness: SIMULATED Evidence-supported candidate (verified readiness: Insufficient evidence)");
    expect(copied).toContain("[SYNTHETIC]");
    expect(screen.getByRole("status")).toHaveTextContent("Copied.");
    // What is copied is what is shown.
    expect(screen.getByTestId("brief-text").textContent).toBe(copied);
  });

  it("the verified view of the same synthetic report counts none of it", () => {
    const brief = buildBrief(synthetic, game(synthetic, "dota-2"), "verified");
    expect(brief.readiness).toBe("insufficient_evidence");
    expect(brief.whyNow).toEqual([]);
    expect(brief.notCounted.length).toBeGreaterThan(0);
    expect(brief.notCounted.every((i) => i.standing !== "verified")).toBe(true);
    const text = briefText(brief);
    expect(text).toContain("NOTE: This report holds synthetic inputs");
    expect(text).toContain("[SYNTHETIC]");
    expect(text).not.toContain(`*** ${SYNTHETIC_WARNING}`);
  });
});

describe("missing evidence stays missing, and provenance stays labelled", () => {
  it("labels unverified evidence wherever it appears", () => {
    const report = variant(synthetic, "rust", (a) => {
      a.unconfirmed = a.unconfirmed.map((s) => ({ ...s, standing: "unverified" as const }));
      a.sources = a.sources.map((s, i) => (i === 0 ? { ...s, standing: "unverified" as const } : s));
    });
    const text = briefText(buildBrief(report, game(report, "rust"), "verified"));
    expect(text).toContain("[UNVERIFIED]");
    expect(text).toContain("NOTE: Some inputs are UNVERIFIED");
    expect(text).toContain("Anything marked UNVERIFIED or SYNTHETIC: treat it as a lead");
  });

  it("an empty but complete creator sample is a lower bound, never low competition", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.creator_coverage = "[VERIFIED] Sample complete: the search returned no videos for this game.";
      a.facts = { ...a.facts, coverage_known: true };
    });
    const brief = buildBrief(report, game(report, "minecraft"), "verified");
    const supply = brief.creatorSupply.join(" ");
    expect(supply).toContain("lower bound");
    expect(supply).toContain("total creator competition is unknown");
    expect(briefText(brief).replace(/not low competition/g, "")).not.toMatch(/low competition/i);
  });

  it("an incomplete creator sample cannot describe competition", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.creator_coverage = "[UNVERIFIED] Incomplete sample: the search stopped before its end.";
      a.facts = { ...a.facts, coverage_known: false };
    });
    const brief = buildBrief(report, game(report, "minecraft"), "verified");
    expect(brief.creatorSupply.join(" ")).toContain("incomplete or not counted");
    expect(brief.confirmFirst.join(" ")).toContain("How many creators already cover it");
  });

  it("no creator or community input is unknown, not low competition or low interest", () => {
    const brief = buildBrief(recorded, game(recorded, "minecraft"), "verified");
    expect(brief.creatorSupply.join(" ")).toContain("That is not low competition");
    expect(brief.community.join(" ")).toContain("That is not low interest");
  });

  it("an unverified or absent event is never a factual why-now", () => {
    const absent = buildBrief(recorded, game(recorded, "minecraft"), "verified");
    expect(absent.notCounted).toEqual([]);
    const withEvent = synthetic.assessments.find((a) => a.events.length > 0)!;
    const brief = buildBrief(synthetic, withEvent, "verified");
    expect(brief.whyNow).toEqual([]);
    const event = brief.notCounted.find((i) => i.text.includes("not counted as a fact"));
    expect(event?.standing).toBe("synthetic");
  });

  it("a missing angle is said to be missing; candidate angles are leads only", () => {
    expect(buildBrief(recorded, game(recorded, "minecraft"), "verified").angleNote).toBe(
      "No video angle is established, and no candidate angle exists.",
    );
    const withAngles = synthetic.assessments.find((a) => a.angles.length > 0)!;
    const brief = buildBrief(synthetic, withAngles, "verified");
    expect(brief.angleNote).toContain("leads only");
    expect(brief.angles.every((i) => i.standing !== "verified")).toBe(true);
  });

  it("shows contradictory demand movements as counterevidence", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.movements = [
        { ...a.movements[0]!, family: "wikimedia_readership", direction: "up", standing: "verified" },
        { ...a.movements[0]!, family: "steam_concurrent_players", direction: "down", standing: "verified" },
      ];
      a.facts = { ...a.facts, moving_up: ["wikimedia_readership"], moving_down: ["steam_concurrent_players"], disagreement: true };
    });
    const brief = buildBrief(report, game(report, "minecraft"), "verified");
    const counter = brief.counterevidence.map((i) => i.text).join(" ");
    expect(counter).toContain("steam concurrent players is DOWN");
    expect(counter).toContain("Demand families disagree");
    expect(brief.conditions.find((c) => c.label.startsWith("No disagreement"))?.met).toBe(false);
  });

  it("keeps the as-of moment, freshness and source references", () => {
    const text = briefText(buildBrief(recorded, game(recorded, "minecraft"), "verified"));
    expect(text).toContain("Assessed as of 2026-10-05 12:00 UTC with creator_opportunity_rules_v1.");
    expect(text).toContain("FRESHNESS");
    expect(text).toMatch(/- wikimedia_pageviews: .*VERIFIED/);
    expect(text).toMatch(/SOURCES REFERENCED\n- /);
    expect(text).toContain("wikimedia_pageviews");
  });
});

describe("the brief is safe and private", () => {
  it("never carries a local filesystem path", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.creator_coverage = "No creator-supply input: read from /Users/someone/.metaspike/private/x.json";
      a.missing = [...a.missing, "see C:\\\\Users\\\\someone\\\\report.json"];
    });
    const text = briefText(buildBrief(report, game(report, "minecraft"), "verified"));
    expect(text).not.toMatch(/\/Users\/|C:\\/);
    expect(text).toContain("[local path removed]");
  });

  it("renders report text as text, never as markup", () => {
    const report = variant(recorded, "minecraft", (a) => {
      a.caveats = ['<script>alert("x")</script><b>bold</b><img src=x onerror=alert(1)>'];
    });
    const { container } = render(<CreatorBrief brief={buildBrief(report, game(report, "minecraft"), "verified")} />);
    expect(container.querySelector("script, b, img")).toBeNull();
    expect(container.textContent).toContain('<script>alert("x")</script>');
    expect(briefText(buildBrief(report, game(report, "minecraft"), "verified"))).toContain("<b>bold</b>");
  });

  it("stays behind the existing private gates", () => {
    expect(enablement({ NODE_ENV: "production", METASPIKE_PRIVATE_OPPORTUNITIES: "1" }).enabled).toBe(false);
    expect(enablement({ NODE_ENV: "development" }).enabled).toBe(false);
    const broken = structuredClone(recordedJson);
    broken.entries[0]!.readiness = "evidence_supported_candidate";
    expect(checkReport(broken).kind).toBe("invalid");
  });

  it("leaves the existing detail sections in place", () => {
    render(<OpportunityDetail report={recorded} slug="minecraft" view="verified" />);
    for (const title of [
      "Creator decision brief",
      "Why now?",
      "What changed?",
      "What event matters?",
      "What video angle is supported?",
      "What creator coverage was observed?",
      "What are players discussing?",
      "What remains unknown?",
      "How current is the evidence?",
      "Why might this be wrong?",
    ]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
  });
});
