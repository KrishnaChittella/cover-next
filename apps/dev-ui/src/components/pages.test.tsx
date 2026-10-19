// Whole pages against the fixture bundle, served through a stand-in for `fetch`.
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { formatTimestamp } from "@/lib/format";
import { bundleFetcher, current, game, manifest, methodology, sourceHealth, type Overrides } from "@/test/bundle";

const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
vi.mock("echarts/core", () => ({ use: vi.fn(), init: vi.fn(() => chart) }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, LegendComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

import { GameDetail } from "./game-detail";
import { Methodology } from "./methodology";
import { Overview } from "./overview";
import { SourceHealth } from "./source-health";

const version = manifest().version_path;

function serve(overrides: Overrides = {}) {
  const { fetcher, requested } = bundleFetcher("/data", overrides);
  vi.stubGlobal("fetch", fetcher);
  return requested;
}

beforeEach(() => {
  chart.setOption.mockClear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

describe("the overview", () => {
  it("renders the run's metadata, the scope notice, both lists and the capability panel", async () => {
    serve();
    render(<Overview />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    const board = current();
    await screen.findByTestId("leaderboard-table");

    const scope = screen.getByRole("complementary", { name: "Scope of the current score" });
    expect(scope).toHaveTextContent(board.scope_note);
    expect(scope).toHaveTextContent("is not the final opportunity score");
    expect(scope).toHaveTextContent("not the finished MetaSpike creator-opportunity model");

    const snapshot = screen.getByRole("region", { name: "Snapshot" });
    expect(snapshot).toHaveTextContent(formatTimestamp(board.generated_at));
    expect(snapshot).toHaveTextContent("3 Oct 2026");
    expect(snapshot).toHaveTextContent(`${board.entries.length} of ${board.entries.length + board.unranked.length}`);
    expect(snapshot).toHaveTextContent("Wikimedia pageviews");
    expect(snapshot).toHaveTextContent("60 / 100");
    expect(snapshot).toHaveTextContent("Degraded");

    expect(within(screen.getByTestId("unranked-list")).getAllByRole("listitem")).toHaveLength(board.unranked.length);

    const capabilities = screen.getByRole("region", { name: "What exists today" });
    for (const missing of ["Event heat", "Multi-source agreement", "Creator supply gap", "Final opportunity score"]) {
      const item = within(capabilities).getByText(missing).closest("li")!;
      expect(item.textContent).not.toMatch(/\d/); // no number beside something that does not exist
    }
    expect(screen.getByText(new RegExp(`run ${manifest().run_id}`))).toBeInTheDocument();
  });

  it("renders nothing from a leaderboard that breaks the schema, and says why", async () => {
    serve({
      [`${version}/current.json`]: (body) => {
        (body as { entries: { score: unknown }[] }).entries[0]!.score = "12.2";
        return body;
      },
    });
    render(<Overview />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("An artifact does not match the contract");
    expect(alert).toHaveTextContent("/entries/0/score");
    expect(alert).toHaveTextContent("npm run contract:generate");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("Minecraft")).not.toBeInTheDocument();
  });

  it("explains how to get data when none has been synced", async () => {
    serve({ "manifest.json": null });
    render(<Overview />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The artifacts could not be loaded");
    expect(alert).toHaveTextContent("npm run data:sync");
    expect(alert).toHaveTextContent("/data");
  });

  it("refuses to mix two runs", async () => {
    serve({ [`${version}/source-health.json`]: (body) => ({ ...(body as object), run_id: "20250101T000000Z-deadbeef" }) });
    render(<Overview />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The artifacts do not belong together");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("a ranked game", () => {
  it("shows its score, components, confidence, reasons, features, coverage and history", async () => {
    serve();
    render(<GameDetail slug="minecraft" />);
    const artifact = game("minecraft");
    expect(await screen.findByRole("heading", { level: 1, name: "Minecraft" })).toBeInTheDocument();
    expect(screen.getByText(artifact.score.score_version, { selector: "dd" })).toBeInTheDocument();

    const meter = (name: string) => screen.getByRole("meter", { name });
    expect(meter("Significance")).toHaveAttribute("aria-valuenow", String(artifact.score.components!.significance));
    expect(meter("Momentum")).toHaveAttribute("aria-valuenow", String(artifact.score.components!.momentum));
    expect(meter("Source ceiling")).toHaveAttribute("aria-valuenow", "0.6");
    expect(meter("Freshness")).toHaveAttribute("aria-valuenow", "1");

    const why = screen.getByRole("region", { name: "Why now" });
    for (const reason of artifact.explanation.reasons) {
      expect(why).toHaveTextContent(reason.code);
      expect(why).toHaveTextContent(reason.text);
    }
    expect(screen.getByRole("region", { name: "Score v0 and confidence" })).toHaveTextContent(
      "not components of a final opportunity score",
    );
    expect(screen.getByRole("link", { name: /Read how metaspike_score_v0/ })).toHaveAttribute("href", "/methodology");

    // The chart is given exactly the published points, with gaps left as gaps.
    await waitFor(() => expect(chart.setOption).toHaveBeenCalledTimes(1));
    const option = chart.setOption.mock.calls[0]![0] as { series: { connectNulls: boolean; data: unknown[] }[] };
    expect(option.series).toHaveLength(2);
    expect(option.series.every((line) => line.connectNulls === false)).toBe(true);
    expect(option.series[0]!.data).toHaveLength(artifact.history[0]!.points.length);
    const figure = screen.getByRole("img");
    expect(figure).toHaveAccessibleName(/90 of 90 days observed/);
    expect(screen.getByText(/Source: Wikimedia pageviews/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is interpolated and nothing is drawn as zero/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Pageviews, daily for Minecraft, by day/ })).toBeInTheDocument();
  });

  it("shows relationships and aliases, linking to the parent game", async () => {
    serve();
    render(<GameDetail slug="gta-vi" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Grand Theft Auto VI" })).toBeInTheDocument();
    expect(screen.getByText(/Also known as GTA VI, GTA 6/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grand Theft Auto V" })).toHaveAttribute("href", "/games/gta-v");
    expect(screen.getByText(/Sequel to/)).toBeInTheDocument();
  });
});

describe("an unscored game", () => {
  it("says it is not scored and shows no number in place of a score, rank or confidence", async () => {
    serve();
    render(<GameDetail slug="fivem" />);
    expect(await screen.findByRole("heading", { level: 1, name: "FiveM" })).toBeInTheDocument();
    expect(screen.getByText("Unranked")).toBeInTheDocument();
    expect(screen.getByText("Not scored")).toBeInTheDocument();
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(screen.getByText(/Not scored: no observations/)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(screen.getByText(/carries no history for this game/)).toBeInTheDocument();
    expect(screen.getByText("No features were computed for this game in this run.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grand Theft Auto V" })).toHaveAttribute("href", "/games/gta-v");
    expect(chart.setOption).not.toHaveBeenCalled();
    const headline = screen.getByText("Unranked").closest("dl")!;
    expect(headline.textContent).not.toMatch(/\b0(\.0)?\b/);
  });

  it("says a game is not in the run for a slug the manifest does not list", async () => {
    serve();
    render(<GameDetail slug="half-life-3" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not in the current run");
  });
});

describe("source health", () => {
  it("shows what each source returned, with failures grouped by cause", async () => {
    serve();
    render(<SourceHealth />);
    const source = sourceHealth().sources[0]!;
    const card = await screen.findByRole("article", { name: /Wikimedia pageviews/ });
    expect(card).toHaveTextContent("Degraded");
    expect(card).toHaveTextContent("Some games were collected and some failed.");
    const stat = (label: string) => within(card).getByText(label).closest("div")!;
    expect(stat("Attempted")).toHaveTextContent(String(source.entities_attempted));
    expect(stat("Succeeded")).toHaveTextContent(String(source.entities_succeeded));
    expect(stat("Failed")).toHaveTextContent(String(source.entities_failed));
    expect(stat("Not mapped")).toHaveTextContent(String(source.entities_not_mapped));
    expect(stat("Expected latest day")).toHaveTextContent("3 Oct 2026");
    expect(stat("Staleness")).toHaveTextContent("current");
    expect(card).toHaveTextContent(`not_found${source.failures.length} games`);
    for (const failure of source.failures) {
      expect(within(card).getByRole("link", { name: failure.slug })).toHaveAttribute("href", `/games/${failure.slug}`);
    }
  });
});

describe("the methodology", () => {
  it("is rendered from the artifact, with the v0 distinction first", async () => {
    serve();
    render(<Methodology />);
    const artifact = methodology();
    const notice = await screen.findByRole("complementary", { name: "What score v0 is" });
    expect(notice).toHaveTextContent("Internal score v0");
    expect(notice).toHaveTextContent("the final MetaSpike creator-opportunity model");
    expect(notice).toHaveTextContent(artifact.scope_note);

    const scoreTable = screen.getByRole("table", { name: "Score terms" });
    for (const term of artifact.score.terms) {
      expect(scoreTable).toHaveTextContent(term.formula);
      expect(scoreTable).toHaveTextContent(term.rationale);
    }
    const confidenceTable = screen.getByRole("table", { name: "Confidence terms" });
    for (const term of artifact.confidence.terms) expect(confidenceTable).toHaveTextContent(term.formula);
    const featureTable = screen.getByRole("table", { name: "Feature definitions" });
    for (const definition of artifact.features.definitions) expect(featureTable).toHaveTextContent(definition.name);

    const limitations = screen.getByRole("region", { name: "Limitations" });
    expect(within(limitations).getAllByRole("listitem")).toHaveLength(artifact.limitations.length);
    for (const limitation of artifact.limitations) expect(limitations).toHaveTextContent(limitation);

    const sources = screen.getByRole("region", { name: "Sources" });
    expect(sources).toHaveTextContent(artifact.sources[0]!.description);
    expect(sources).toHaveTextContent(artifact.sources[0]!.policy.attribution_text!);
    expect(screen.getByRole("region", { name: "Confidence" })).toHaveTextContent("60");
  });
});
