// The validation page: the real offline backtest of the recorded replay, rendered as it
// came out, and SYNTHETIC variations that exist only here to exercise other states.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ValidationArtifact, ValidationReportView } from "@/contract";
import { validationViolations, violations } from "@/contract/validate";
import { loadValidation } from "@/lib/data/client";
import { formatCorrelation, formatOutcome, formatRate } from "@/lib/format";
import { bundleFetcher, manifest, validation, type Overrides } from "@/test/bundle";

vi.mock("next/navigation", () => ({ usePathname: () => "/validation" }));

import { SiteHeader } from "./site-header";
import { Validation, ValidationBody } from "./validation";

const version = manifest().version_path;
const real = validation();
const report = real.report as ValidationReportView;

function serve(overrides: Overrides = {}) {
  const { fetcher } = bundleFetcher("/data", overrides);
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function withReport(change: (draft: ValidationReportView) => void): ValidationArtifact {
  const copy = structuredClone(real);
  change(copy.report as ValidationReportView);
  return copy;
}

const strategyRows = (strategy: string) =>
  within(document.querySelector(`[data-strategy="${strategy}"]`) as HTMLElement).getAllByRole("row");

describe("formatting an outcome", () => {
  it("shows a fraction as a signed percentage and an unevaluable one as absent", () => {
    expect(formatOutcome(1.2926)).toBe("+129.3%");
    expect(formatOutcome(0.0549)).toBe("+5.5%");
    expect(formatOutcome(-0.4878)).toBe("−48.8%");
    expect(formatOutcome(0)).toBe("0.0%");
    expect(formatOutcome(null)).toBe("—");
    expect(formatOutcome(undefined)).toBe("—");
    expect(formatRate(0.8358)).toBe("84%");
    expect(formatRate(0)).toBe("0%");
    expect(formatRate(null)).toBe("—");
    expect(formatCorrelation(0.679)).toBe("+0.68");
    expect(formatCorrelation(-0.143)).toBe("−0.14");
    expect(formatCorrelation(0)).toBe("0.00");
    expect(formatCorrelation(null)).toBe("—");
  });
});

describe("the real replay backtest", () => {
  it("is a valid artifact and is honest about being framework only", () => {
    expect(violations("validation", real)).toEqual([]);
    expect(validationViolations(real)).toEqual([]);
    expect(real.availability).toBe("available");
    expect(report.evidence.status).toBe("framework_only");
    expect(report.evidence.checks.some((check) => check.reached)).toBe(false);
    expect(report.games_with_outcome).toBe(6);
    expect(report.events.real_event_history).toBe(false);
    expect(JSON.stringify(real)).not.toMatch(/synthetic/i);
  });

  it("leads with the evidence status and says the sample proves nothing about the score", async () => {
    serve();
    render(<Validation />);
    const status = await screen.findByTestId("evidence-status");
    expect(status).toHaveAttribute("data-status", "framework_only");
    expect(status).toHaveTextContent("Framework only");
    expect(status).toHaveTextContent("The machinery works. Nothing is shown about the score.");
    expect(status).toHaveTextContent(report.evidence.conclusion);
    expect(status).toHaveTextContent("too small to say anything about whether the score predicts demand");
    expect(status).toHaveTextContent(real.scope_note);
    expect(status).not.toHaveTextContent(/validated on/i);
    // It comes before any result on the page.
    const table = screen.getByTestId("strategy-table");
    expect(status.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows what was tested, with the counts from the report", async () => {
    serve();
    render(<Validation />);
    const tested = await screen.findByRole("region", { name: "What was tested" });
    const stat = (label: string) => within(tested).getByText(label).closest("div")!;
    expect(stat("Recommendation days")).toHaveTextContent("91");
    expect(stat("Recommendation days")).toHaveTextContent("7 Jul 2026 to 5 Oct 2026");
    expect(stat("Recording ends")).toHaveTextContent("3 Oct 2026");
    expect(stat("Games ranked at least once")).toHaveTextContent("6");
    expect(stat("Games ranked at least once")).toHaveTextContent("of 15 in the universe");
    expect(stat("Snapshots")).toHaveTextContent("1,365");
    expect(stat("Snapshots")).toHaveTextContent("420 ranked, 945 unranked");
    expect(stat("Outcomes evaluable")).toHaveTextContent("396");
    expect(stat("Outcomes evaluable")).toHaveTextContent("24 future incomplete");
    expect(stat("Independent sources")).toHaveTextContent("1");
    expect(stat("Score under test")).toHaveTextContent("metaspike_score_v0");
  });

  it("says when each recommendation was made and the newest day it could see", async () => {
    serve();
    render(<Validation />);
    const timing = await screen.findByTestId("timing");
    const stat = (label: string) => within(timing).getByText(label).closest("div")!;
    // Recommendations run 7 Jul to 5 Oct; with a one-day lag each sees two days back.
    expect(stat("Newest demand day visible")).toHaveTextContent("5 Jul 2026 to 3 Oct 2026");
    expect(stat("Source publication lag")).toHaveTextContent("Wikimedia pageviews: 24 h");
    expect(screen.getByRole("region", { name: "What each recommendation could see" })).toHaveTextContent(
      "had ended but was not yet published is left out of the recommendation and is not part of its outcome either",
    );
    expect(report.publication_lag_hours).toEqual({ wikimedia_pageviews: 24 });
    for (const day of report.inspection) {
      expect(day.latest_due_day < day.day).toBe(true);
      expect(day.primary_window_start).toBe(day.day);
      expect(day.as_of).toBe(`${day.day}T00:00:00Z`);
    }
  });

  it("names the records it summarises", async () => {
    serve();
    render(<Validation />);
    const provenance = await screen.findByTestId("provenance");
    expect(report.snapshot_set_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(report.label_set_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(report.snapshot_set_hash).not.toBe(report.label_set_hash);
    expect(provenance).toHaveTextContent(report.snapshot_set_hash);
    expect(provenance).toHaveTextContent(report.label_set_hash);
    expect(provenance).toHaveTextContent(report.report_id);
    expect(provenance).toHaveTextContent("validation_report_v2");
    expect(provenance).toHaveTextContent("Snapshots (1,365)");
  });

  it("shows score v0 beside the simple alternatives, exactly as computed", async () => {
    serve();
    render(<Validation />);
    await screen.findByTestId("strategy-table");
    expect(report.strategies.map((strategy) => strategy.strategy)).toEqual([
      "metaspike_score_v0",
      "raw_popularity",
      "simple_lift",
      "simple_velocity",
    ]);
    for (const strategy of report.strategies) {
      const rows = strategyRows(strategy.strategy);
      expect(rows).toHaveLength(3);
      strategy.top_k.forEach((group, index) => {
        const row = rows[index]!;
        expect(row).toHaveTextContent(`Top ${group.k}`);
        expect(row).toHaveTextContent(formatOutcome(group.outcomes.mean));
        expect(row).toHaveTextContent(formatOutcome(group.outcomes.median));
        expect(row).toHaveTextContent(formatRate(group.outcomes.hit_rate));
      });
      expect(rows[0]).toHaveTextContent(strategy.label);
      expect(rows[0]).toHaveTextContent(formatCorrelation(strategy.association.mean_rho));
      expect(rows[0]).toHaveTextContent(`${strategy.association.days_used} days`);
    }
    expect(strategyRows("metaspike_score_v0")[0]).toHaveTextContent("under test");
    expect(strategyRows("simple_lift")[0]).not.toHaveTextContent("under test");
  });

  it("does not hide that plain lift orders games at least as well as score v0 here", async () => {
    serve();
    render(<Validation />);
    await screen.findByTestId("strategy-table");
    const rho = (name: string) => report.strategies.find((s) => s.strategy === name)!.association.mean_rho!;
    expect(rho("simple_lift")).toBeGreaterThanOrEqual(rho("metaspike_score_v0"));
    expect(strategyRows("simple_lift")[0]).toHaveTextContent(formatCorrelation(rho("simple_lift")));
    expect(strategyRows("metaspike_score_v0")[0]).toHaveTextContent(formatCorrelation(rho("metaspike_score_v0")));
    // No ordering by result, no winner, no praise: the strategies are in the artifact's order.
    const order = [...document.querySelectorAll("[data-strategy]")].map((node) => node.getAttribute("data-strategy"));
    expect(order).toEqual(report.strategies.map((strategy) => strategy.strategy));
    expect(document.body.textContent).not.toMatch(/outperform|beats|winner|best strategy|\bproven\b|predictive power/i);
  });

  it("shows an empty stratum as empty, never as zero", async () => {
    serve();
    render(<Validation />);
    const bands = await screen.findByRole("table", { name: "Primary outcome by confidence band" });
    const high = within(bands).getByRole("rowheader", { name: "High" }).closest("tr")!;
    const cells = within(high).getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells[0]).toBe("0"); // the count is a real zero
    expect(cells.slice(1)).toEqual(["—not available", "—not available", "—", "—"]);
    const bins = screen.getByRole("table", { name: "Primary outcome by score bin" });
    expect(within(bins).getAllByRole("row")).toHaveLength(report.score_bins.length + 1);
    const zero = within(bins).getByRole("rowheader", { name: "0" }).closest("tr")!;
    expect(zero).toHaveTextContent(formatOutcome(report.score_bins[0]!.outcomes.mean));
    expect(report.score_bins[0]!.outcomes.mean).toBeLessThan(0); // a negative mean, shown
    expect(zero).toHaveTextContent("−");
  });

  it("explains the status by its requirements and shows the look-ahead checks", async () => {
    serve();
    render(<Validation />);
    const evidence = await screen.findByTestId("evidence-table");
    const rows = within(evidence).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Insufficient sample");
    expect(rows[0]).toHaveTextContent("No");
    expect(rows[0]).toHaveTextContent("6 / 20"); // six games where twenty are needed
    expect(rows[0]).toHaveTextContent("91 / 30");
    expect(rows[2]).toHaveTextContent("Validated");
    expect(rows[2]).toHaveTextContent("held_out_period");
    expect(rows[2]).toHaveTextContent("0 / 1");
    expect(screen.getByRole("region", { name: "Why this status" })).toHaveTextContent("cannot report validated");
    const checks = within(screen.getByRole("region", { name: "Look-ahead checks" })).getAllByRole("listitem");
    expect(checks).toHaveLength(5);
    for (const check of checks) expect(check).toHaveTextContent("Passed");
    expect(checks[0]).toHaveTextContent("source_publication_lag_respected");
    expect(checks[0]).toHaveTextContent("546 checked");
    expect(checks[1]).toHaveTextContent("outcome_window_after_prediction");
    expect(checks[3]).toHaveTextContent("unchanged_when_unseen_values_altered");
    expect(checks[3]).toHaveTextContent("days that had ended but were not yet published");
  });

  it("says event backtesting is unavailable and lists every limitation", async () => {
    serve();
    render(<Validation />);
    const events = await screen.findByRole("complementary", { name: "Event backtesting" });
    expect(events).toHaveTextContent("Event backtesting unavailable");
    expect(events).toHaveTextContent(report.events.note);
    expect(events).toHaveTextContent("0 of 1,365");
    expect(events).toHaveTextContent("event_heat_rules_v1");
    const limitations = screen.getByRole("region", { name: "Limitations" });
    expect(within(limitations).getAllByRole("listitem")).toHaveLength(report.limitations.length);
    for (const limitation of report.limitations) expect(limitations).toHaveTextContent(limitation);
    const outcomes = screen.getByRole("table", { name: "Outcome definitions" });
    for (const term of real.outcome_definition.terms) expect(outcomes).toHaveTextContent(term.formula);
    expect(outcomes).toHaveTextContent("future_incomplete");
  });

  it("lets a past day be inspected: what was ranked, and what followed", async () => {
    const user = userEvent.setup();
    serve();
    render(<Validation />);
    const table = await screen.findByTestId("inspection-table");
    const latest = report.inspection[report.inspection.length - 1]!;
    expect(screen.getByRole("combobox", { name: "Recommendation day" })).toHaveValue(latest.day);
    // The last recorded day has no future yet: said in words, not as a number.
    let rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(latest.ranked.length);
    expect(rows[0]).toHaveTextContent("Minecraft");
    expect(rows[0]).toHaveTextContent("12.2");
    expect(rows[0]).toHaveTextContent("Future incomplete");
    expect(rows[0]!.textContent).not.toMatch(/%/);
    expect(screen.getByText(/Unranked that day \(9\)/)).toBeInTheDocument();
    let timing = screen.getByTestId("inspection-timing");
    expect(latest.day).toBe("2026-10-05");
    expect(timing).toHaveTextContent("5 Oct 2026, 00:00 UTC");
    expect(within(timing).getByText("Newest demand day visible then").closest("div")).toHaveTextContent("3 Oct 2026");
    expect(timing).toHaveTextContent("5 Oct 2026 to 7 Oct 2026");

    const earlier = report.inspection.find((entry) => entry.day === "2026-09-15")!;
    await user.selectOptions(screen.getByRole("combobox", { name: "Recommendation day" }), earlier.day);
    rows = within(screen.getByTestId("inspection-table")).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("link").textContent)).toEqual(earlier.ranked.map((entry) => entry.name));
    earlier.ranked.forEach((entry, index) => {
      const row = rows[index]!;
      expect(row).toHaveTextContent(String(entry.rank));
      expect(row).toHaveTextContent(formatOutcome(entry.realized_uplift_72h));
      expect(within(row).getByRole("link")).toHaveAttribute("href", `/games/${entry.slug}`);
    });
    expect(earlier.ranked.some((entry) => (entry.realized_uplift_72h ?? 0) < 0)).toBe(true); // negatives are there
    timing = screen.getByTestId("inspection-timing");
    expect(timing).toHaveTextContent("15 Sep 2026, 00:00 UTC");
    expect(within(timing).getByText("Newest demand day visible then").closest("div")).toHaveTextContent("13 Sep 2026");
    expect(timing).toHaveTextContent("15 Sep 2026 to 17 Sep 2026");
  });
});

describe("other states (synthetic, tests only)", () => {
  it("renders a bad result as a bad result", () => {
    const bad = withReport((draft) => {
      const v0 = draft.strategies[0]!;
      v0.top_k[0]!.outcomes = { count: 40, mean: -0.213, median: -0.18, minimum: -0.6, maximum: 0.05, hit_rate: 0.05, downside_rate: 0.7 };
      v0.association = { ...v0.association, mean_rho: -0.41, positive_days: 9, negative_days: 51 };
    });
    expect(violations("validation", bad)).toEqual([]);
    render(<ValidationBody validation={bad} />);
    const row = strategyRows("metaspike_score_v0")[0]!;
    expect(row).toHaveTextContent("−21.3%");
    expect(row).toHaveTextContent("−18.0%");
    expect(row).toHaveTextContent("5%");
    expect(row).toHaveTextContent("70%");
    expect(row).toHaveTextContent("−0.41");
    expect(row).toHaveTextContent("9 positive, 51 negative");
    expect(screen.getByTestId("evidence-status")).toHaveTextContent("Framework only");
  });

  it("shows 'not run' as its own state with no result at all", () => {
    const notRun: ValidationArtifact = { ...structuredClone(real), availability: "not_run", report: null };
    expect(violations("validation", notRun)).toEqual([]);
    expect(validationViolations(notRun)).toEqual([]);
    render(<ValidationBody validation={notRun} />);
    const state = screen.getByTestId("validation-not-run");
    expect(state).toHaveTextContent("Historical validation has not been run");
    expect(state).toHaveTextContent("This is not a result.");
    expect(state).toHaveTextContent("not zero, not good, not bad");
    // No measured figure of any kind. (The score's own name, "…_v0", is the only digit.)
    expect(state.textContent!.replace("metaspike_score_v0", "")).not.toMatch(/\d/);
    expect(state.textContent).not.toMatch(/%/);
    expect(screen.queryByTestId("strategy-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("evidence-status")).not.toBeInTheDocument();
    // The definition of the outcome is still shown: it does not depend on a run.
    expect(screen.getByRole("table", { name: "Outcome definitions" })).toBeInTheDocument();
  });

  it("shows a larger sample's status as what it is, and never as validated under this protocol", () => {
    const larger = withReport((draft) => {
      draft.evidence.status = "insufficient_sample";
      draft.evidence.checks[0]!.reached = true;
      draft.evidence.checks[0]!.requirements.forEach((item) => {
        item.actual = item.required;
        item.met = true;
      });
      draft.evidence.conclusion = "Insufficient sample. Every metric can be computed, but the sample is too small.";
    });
    expect(validationViolations(larger)).toEqual([]);
    render(<ValidationBody validation={larger} />);
    const status = screen.getByTestId("evidence-status");
    expect(status).toHaveAttribute("data-status", "insufficient_sample");
    expect(status).toHaveTextContent("Every metric computes. None supports a conclusion.");
  });
});

describe("refusing a report that claims more than it shows", () => {
  const cases: [string, (draft: ValidationArtifact) => void, string][] = [
    ["a status its checks do not support", (draft) => void (draft.report!.evidence.status = "validated"), "/report/evidence/status"],
    [
      "a status marked reached with an unmet requirement",
      (draft) => {
        draft.report!.evidence.checks[0]!.reached = true;
        draft.report!.evidence.status = "insufficient_sample";
      },
      "/report/evidence/checks",
    ],
    ["a failed look-ahead check", (draft) => void (draft.report!.leakage_checks[0]!.passed = false), "/report/leakage_checks"],
    ["no look-ahead checks at all", (draft) => void (draft.report!.leakage_checks = []), "/report/leakage_checks"],
    [
      "a report whose publication lag was never checked",
      (draft) => void (draft.report!.leakage_checks = draft.report!.leakage_checks.slice(1)),
      "/report/leakage_checks",
    ],
    [
      "a recommendation that saw its own day",
      (draft) => void (draft.report!.inspection[0]!.latest_due_day = draft.report!.inspection[0]!.day),
      "/report/inspection/0/latest_due_day",
    ],
    [
      "an outcome window that starts before the recommendation",
      (draft) => void (draft.report!.inspection[0]!.primary_window_start = draft.report!.inspection[0]!.latest_due_day),
      "/report/inspection/0/primary_window_start",
    ],
    ["a provenance hash that is not one", (draft) => void (draft.report!.snapshot_set_hash = "unknown"), "/report/snapshot_set_hash"],
    ["'not run' that still carries a report", (draft) => void (draft.availability = "not_run"), "/report"],
    ["'available' with no report", (draft) => void (draft.report = null), "/report"],
  ];

  it.each(cases)("rejects %s", async (_name, change, path) => {
    const { fetcher } = bundleFetcher("/data", {
      [`${version}/validation.json`]: (body) => {
        change(body as ValidationArtifact);
        return body;
      },
    });
    await expect(loadValidation({ baseUrl: "/data", fetcher })).rejects.toMatchObject({
      kind: "invalid",
      details: expect.arrayContaining([expect.objectContaining({ path })]),
    });
  });

  it("renders nothing from such a report", async () => {
    serve({
      [`${version}/validation.json`]: (body) => {
        (body as ValidationArtifact).report!.evidence.status = "validated";
        return body;
      },
    });
    render(<Validation />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("An artifact does not match the contract");
    expect(alert).toHaveTextContent("claims validated; its checks support framework_only");
    expect(screen.queryByTestId("strategy-table")).not.toBeInTheDocument();
  });

  it("rejects a schema-invalid report and a missing file", async () => {
    const wrong = bundleFetcher("/data", {
      [`${version}/validation.json`]: (body) => {
        (body as { report: { evidence: { status: string } } }).report.evidence.status = "proven";
        return body;
      },
    });
    await expect(loadValidation({ baseUrl: "/data", fetcher: wrong.fetcher })).rejects.toMatchObject({ kind: "invalid" });
    const missing = bundleFetcher("/data", { [`${version}/validation.json`]: null });
    await expect(loadValidation({ baseUrl: "/data", fetcher: missing.fetcher })).rejects.toMatchObject({ kind: "unavailable" });
  });
});

describe("navigation", () => {
  it("includes validation among the private pages", () => {
    render(<SiteHeader />);
    const link = within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: "Validation" });
    expect(link).toHaveAttribute("href", "/validation");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
