import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { parseView, viewQuery } from "@/private/opportunities/loader";
import type { OpportunityReport } from "@/private/opportunities/types";
import { checkReport } from "@/private/opportunities/validate";
import integratedJson from "@/test/fixtures/opportunities/integrated.json";

import { INTEGRATED_HEADLINE, NO_REAL_OPPORTUNITY, OpportunityBoard, OpportunityDetail } from "./opportunities";

const integrated = integratedJson as unknown as OpportunityReport;

function source(slug: string, name: string) {
  const a = integrated.assessments.find((item) => item.slug === slug)!;
  return a.sources.find((s) => s.source === name)!;
}

describe("the integrated recorded-sources report", () => {
  it("is a valid private report that reads genuine recorded inputs and nothing invented", () => {
    expect(checkReport(integratedJson).kind).toBe("ok");
    expect(integrated.distribution).toBe("private_development_only");
    expect(integrated.contains_synthetic).toBe(false);
    expect(integrated.contains_unverified).toBe(true);
    for (const a of integrated.assessments) {
      for (const s of a.sources) expect(s.standing).not.toBe("synthetic");
    }
  });

  it("carries each source to the game it is bound to, with its own standing and freshness", () => {
    // The one recorded Steam response: Counter-Strike 2's app, dated when Steam answered.
    const players = source("counter-strike-2", "steam_current_players");
    expect(players).toMatchObject({ kind: "demand_signal", state: "available", standing: "unverified" });
    expect(players.latest_observed_at?.startsWith("2026-10-06T01:25:26")).toBe(true);
    // Pinned pageviews, one due day short at this moment: stale, and still verified.
    expect(source("minecraft", "wikimedia_pageviews")).toMatchObject({ state: "stale", standing: "verified" });
    // Pinned catalogue records are identity, never demand.
    expect(source("counter-strike-2", "igdb")).toMatchObject({ kind: "catalogue_metadata", standing: "verified" });
    // Another eligible Steam app was never recorded: missing, never filled in.
    expect(source("dota-2", "steam_current_players")).toMatchObject({ state: "missing", latest_observed_at: null });
    // Sources with no input at all stay not integrated.
    expect(source("counter-strike-2", "creator_supply")).toMatchObject({ state: "not_integrated", standing: "none" });
  });

  it("counts nothing unverified: no game is more than insufficient", () => {
    for (const entry of integrated.entries) expect(entry.readiness).toBe("insufficient_evidence");
    const cs2 = integrated.assessments.find((a) => a.slug === "counter-strike-2")!;
    expect(cs2.why_now).toEqual([]);
  });

  it("shows the board with an unmistakable notice and each game's sources", () => {
    render(<OpportunityBoard report={integrated} view="integrated" />);
    const banner = screen.getByTestId("integrated-banner");
    expect(banner).toHaveTextContent(INTEGRATED_HEADLINE);
    expect(banner).toHaveTextContent("unverified origin");
    expect(banner).toHaveTextContent("steam_current_players");
    expect(screen.getByTestId("no-real-opportunity")).toHaveTextContent(NO_REAL_OPPORTUNITY);
    expect(screen.queryByTestId("simulated-shortlist")).toBeNull();
    const row = screen
      .getAllByTestId("shortlist-row")
      .find((r) => r.textContent?.includes("Counter-Strike 2"))!;
    const cell = within(row).getByTestId("evidence-cell");
    expect(cell).toHaveTextContent("steam_current_players available");
    expect(cell).toHaveTextContent("UNVERIFIED");
    expect(cell).toHaveTextContent("wikimedia_pageviews missing");
    // Links keep the view.
    expect(within(row).getByRole("link").getAttribute("href")).toBe("/opportunities/counter-strike-2?view=integrated");
  });

  it("shows a game's source table with kind, state, latest observation, freshness and origin", () => {
    render(<OpportunityDetail report={integrated} slug="counter-strike-2" view="integrated" />);
    const table = screen.getByTestId("freshness");
    const players = within(table)
      .getAllByRole("row")
      .find((r) => r.textContent?.includes("steam_current_players"))!;
    expect(players).toHaveTextContent("demand signal");
    expect(players).toHaveTextContent("available");
    expect(players).toHaveTextContent("2026-10-06 01:25");
    expect(players).toHaveTextContent("fresh");
    expect(players).toHaveTextContent("UNVERIFIED");
    const igdb = within(table).getAllByRole("row").find((r) => r.textContent?.includes("igdb"))!;
    expect(igdb).toHaveTextContent("catalogue metadata");
    expect(igdb).toHaveTextContent("verified");
  });

  it("is selected only by the exact view word", () => {
    expect(parseView("integrated")).toBe("integrated");
    expect(parseView("Integrated")).toBe("verified");
    expect(parseView(["integrated"])).toBe("verified");
    expect(viewQuery("integrated")).toBe("?view=integrated");
    expect(viewQuery("verified")).toBe("");
  });
});
