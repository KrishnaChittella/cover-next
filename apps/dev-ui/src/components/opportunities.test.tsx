import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { OpportunityReport } from "@/private/opportunities/types";
import { checkReport } from "@/private/opportunities/validate";
import recordedJson from "@/test/fixtures/opportunities/recorded.json";
import syntheticJson from "@/test/fixtures/opportunities/synthetic.json";

import { NO_REAL_OPPORTUNITY, NotShown, OpportunityBoard, OpportunityDetail, SYNTHETIC_HEADLINE } from "./opportunities";

const recorded = recordedJson as unknown as OpportunityReport;
const synthetic = syntheticJson as unknown as OpportunityReport;

function rows(testId: string) {
  return within(screen.getByTestId(testId)).getAllByTestId("shortlist-row");
}

describe("the opportunity shortlist", () => {
  it("states plainly that verified evidence establishes nothing today", () => {
    render(<OpportunityBoard report={recorded} view="verified" />);
    expect(screen.getByTestId("no-real-opportunity")).toHaveTextContent(NO_REAL_OPPORTUNITY);
    expect(screen.queryByTestId("synthetic-banner")).toBeNull();
    expect(screen.queryByTestId("simulated-shortlist")).toBeNull();
    expect(document.body.textContent).not.toContain("SIMULATED");
    // The as-of moment, the rules version, and that its thresholds are unvalidated.
    expect(document.body).toHaveTextContent("2026-10-05 12:00 UTC");
    expect(document.body).toHaveTextContent("creator_opportunity_rules_v1");
    expect(document.body).toHaveTextContent("unvalidated");
  });

  it("keeps the engine's order and says when a tie is only an id", () => {
    render(<OpportunityBoard report={synthetic} view="verified" />);
    const shown = rows("verified-shortlist").map((row) => row.querySelector("a")?.textContent);
    const expected = synthetic.entries.map(
      (entry) => synthetic.assessments.find((a) => a.canonical_game_id === entry.canonical_game_id)!.name,
    );
    expect(shown).toEqual(expected);
    expect(document.body).toHaveTextContent("Indistinguishable on the evidence — ordered by id only, which means nothing");
    // A tie is shown as "=", not as a higher rank.
    expect(rows("verified-shortlist").some((row) => row.textContent?.startsWith("="))).toBe(true);
    expect(document.body.textContent).not.toMatch(/\d+(\.\d+)?%/); // no invented percentages
  });

  it("never shows simulated readiness as a game's verified status", () => {
    render(<OpportunityBoard report={synthetic} view="verified" />);
    // Verified view of a report with synthetic inputs: a warning, verified readiness only.
    expect(document.body).toHaveTextContent("This report contains synthetic inputs.");
    for (const row of rows("verified-shortlist")) {
      expect(row).toHaveTextContent("Insufficient evidence");
      expect(row).not.toHaveTextContent("Evidence-supported candidate");
    }
  });

  it("separates the synthetic demonstration, unmistakably", () => {
    render(<OpportunityBoard report={synthetic} view="synthetic" />);
    expect(screen.getByTestId("synthetic-banner")).toHaveTextContent(SYNTHETIC_HEADLINE);
    const simulated = rows("simulated-shortlist");
    expect(simulated[0]).toHaveTextContent("Dota 2");
    expect(simulated[0]).toHaveTextContent("SIMULATED: Evidence-supported candidate");
    expect(simulated[0]).toHaveTextContent("[SYNTHETIC]");
    // The verified table is still there, and still says nothing is established.
    for (const row of rows("verified-shortlist")) expect(row).toHaveTextContent("Insufficient evidence");
    expect(screen.getByTestId("no-real-opportunity")).toBeInTheDocument();
    // Links stay in the synthetic view.
    expect(simulated[0]!.querySelector("a")).toHaveAttribute("href", "/opportunities/dota-2?view=synthetic");
  });
});

describe("a game's opportunity", () => {
  it("answers why, why now, what changed, the angle, coverage, discussion and what is unknown", () => {
    render(<OpportunityDetail report={synthetic} slug="rust" view="synthetic" />);
    for (const heading of [
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
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    expect(document.body).toHaveTextContent("Nothing verified says “now”.");
    expect(document.body).toHaveTextContent("Demand families disagree: one is up while another is down.");
    // Angles: what supports them, and that they are not confirmed.
    const angles = screen.getAllByTestId("angle");
    expect(angles.length).toBeGreaterThanOrEqual(2);
    expect(angles[0]).toHaveTextContent("version 2.3");
    expect(angles[0]).toHaveTextContent("synr003");
    expect(angles[0]).toHaveTextContent("Confirmed: no");
    expect(angles[0]).toHaveTextContent("SYNTHETIC");
    expect(screen.getByTestId("angle-missing")).toHaveTextContent("not yet established on verified evidence");
    expect(document.body).toHaveTextContent("Incomplete sample");
    // Freshness and provenance for each source.
    const freshness = screen.getByTestId("freshness");
    expect(freshness).toHaveTextContent("youtube_data");
    expect(freshness).toHaveTextContent("verified");
    expect(freshness).toHaveTextContent("SYNTHETIC");
    expect(document.body).toHaveTextContent("SIMULATED: Provisional · watchlist");
    expect(document.body).toHaveTextContent("not a recommendation");
  });

  it("says when there is no angle and never calls missing evidence low", () => {
    render(<OpportunityDetail report={recorded} slug="minecraft" view="verified" />);
    expect(screen.getByTestId("angle-missing")).toHaveTextContent("Angle not yet established.");
    const text = document.body.textContent ?? "";
    expect(text).toContain("No creator-supply input");
    expect(text).toContain("No community input");
    // Every mention of low competition or low interest is a denial of it.
    const affirmed = text.replace(/(not|never|not evidence of) low (competition|interest)/gi, "");
    expect(affirmed).not.toMatch(/low competition/i);
    expect(affirmed).not.toMatch(/low (community )?interest/i);
    expect(text).toContain("flat");
    expect(text).not.toContain("SIMULATED");
  });

  it("shows stale and unverified sources as such", () => {
    render(<OpportunityDetail report={synthetic} slug="counter-strike-2" view="synthetic" />);
    expect(document.body).toHaveTextContent("stale");
    expect(document.body).toHaveTextContent("down");
  });

  it("refuses an unknown game rather than guessing", () => {
    render(<OpportunityDetail report={recorded} slug="not-a-game" view="verified" />);
    expect(screen.getByRole("alert")).toHaveTextContent("No game with that slug");
  });
});

describe("when nothing can be shown", () => {
  it("says why, and shows nothing from the report", () => {
    const { rerender } = render(<NotShown loaded={{ kind: "disabled", reason: "disabled in production" }} />);
    expect(screen.getByTestId("report-disabled")).toHaveTextContent("disabled in production");
    rerender(<NotShown loaded={{ kind: "absent", file: "/x/verified.json", view: "verified" }} />);
    expect(screen.getByTestId("report-absent")).toHaveTextContent("npm run opportunities:recorded");
    rerender(<NotShown loaded={{ kind: "unsupported_version", file: "/x", version: "creator_opportunity_rules_v9" }} />);
    expect(screen.getByTestId("report-unsupported_version")).toHaveTextContent("creator_opportunity_rules_v9");
    rerender(<NotShown loaded={{ kind: "invalid", file: "/x", details: [{ path: "/entries", message: "bad" }] }} />);
    expect(screen.getByTestId("report-invalid")).toHaveTextContent("/entries");
  });

  it("refuses to render a report with a duplicated game: the page never sees its entries", () => {
    const broken = structuredClone(syntheticJson);
    broken.simulated_entries[1]!.canonical_game_id = broken.simulated_entries[0]!.canonical_game_id;
    const checked = checkReport(broken);
    expect(checked.kind).toBe("invalid");
    if (checked.kind !== "invalid") return;
    render(<NotShown loaded={{ kind: "invalid", file: "/x/synthetic.json", details: checked.details }} />);
    expect(screen.getByTestId("report-invalid")).toHaveTextContent("names the same game more than once");
    expect(screen.queryByTestId("shortlist-row")).toBeNull();
    expect(screen.queryByTestId("simulated-shortlist")).toBeNull();
  });
});
