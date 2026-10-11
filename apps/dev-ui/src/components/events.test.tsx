// Event intelligence and the radar in the interface. The real replay bundle has no
// event coverage, so its pages must say "unavailable". The populated states are driven
// by SYNTHETIC data that exists only in tests.
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { eventIntelligenceViolations, radarViolations, violations } from "@/contract/validate";
import { loadGame, loadRadar } from "@/lib/data/client";
import { eventHeatState, eventTiming, formatHoursAhead, windowLabel } from "@/lib/events";
import { formatHeat, formatShare } from "@/lib/format";
import { bundleFetcher, game, manifest, methodology, radar, type Overrides } from "@/test/bundle";
import {
  AVAILABLE_TINY_POSITIVE,
  AVAILABLE_WITH_EVENTS,
  AVAILABLE_WITH_NOTHING,
  RADAR_ITEMS,
  SYNTHETIC_EXPEDITION,
  SYNTHETIC_PATCH,
  SYNTHETIC_TRAILER,
  UNAVAILABLE_STALE,
  availableRadar,
} from "@/test/synthetic-events";

const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
vi.mock("echarts/core", () => ({ use: vi.fn(), init: vi.fn(() => chart) }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, LegendComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("next/navigation", () => ({ usePathname: () => "/radar" }));

import { EventPanel } from "./event-panel";
import { GameDetail } from "./game-detail";
import { Methodology } from "./methodology";
import { Radar, RadarBody } from "./radar";
import { SiteHeader } from "./site-header";

const version = manifest().version_path;

function serve(overrides: Overrides = {}) {
  const { fetcher } = bundleFetcher("/data", overrides);
  vi.stubGlobal("fetch", fetcher);
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

describe("the synthetic fixtures", () => {
  it("are valid instances of the contract, so the tests exercise real shapes", () => {
    for (const events of [AVAILABLE_WITH_EVENTS, AVAILABLE_WITH_NOTHING, AVAILABLE_TINY_POSITIVE, UNAVAILABLE_STALE]) {
      expect(violations("game", { ...game("minecraft"), event_intelligence: events })).toEqual([]);
      expect(eventIntelligenceViolations(events)).toEqual([]);
    }
    for (const items of [RADAR_ITEMS, []]) {
      const synthetic = availableRadar(radar(), items);
      expect(violations("radar", synthetic)).toEqual([]);
      expect(radarViolations(synthetic)).toEqual([]);
    }
  });

  it("are never in the bundle the developer UI serves", () => {
    const published = JSON.stringify([radar(), ...Object.keys(manifest().artifacts.games).map(game)]);
    expect(published).not.toMatch(/synthetic/i);
  });
});

describe("the real replay bundle", () => {
  it("has no event coverage: every game is unavailable with a null heat", () => {
    for (const slug of Object.keys(manifest().artifacts.games)) {
      const events = game(slug).event_intelligence;
      expect(events.availability).toBe("unavailable");
      expect(events.event_heat).toBeNull();
      expect(events.contributions).toEqual([]);
      expect(eventHeatState(events)).toEqual({ kind: "unavailable", reasons: ["no_event_coverage"] });
    }
    expect(radar().availability).toBe("unavailable");
    expect(radar().items).toEqual([]);
  });

  it("shows a game's event intelligence as unavailable, with no number in its place", async () => {
    serve();
    render(<GameDetail slug="minecraft" />);
    await screen.findByRole("heading", { level: 1, name: "Minecraft" });
    const panel = screen.getByTestId("event-panel");
    expect(panel).toHaveAttribute("data-availability", "unavailable");
    expect(within(panel).getByText("Unavailable")).toBeInTheDocument();
    expect(panel).toHaveTextContent("no source has looked for this game's events");
    expect(panel).toHaveTextContent("no_event_coverage");
    expect(panel).toHaveTextContent("This is not a heat of zero.");
    expect(panel).toHaveTextContent("event_heat_rules_v1");
    expect(panel).toHaveTextContent("is not part of it");
    expect(within(panel).queryByRole("table")).not.toBeInTheDocument();
    // No heat figure of any kind: not 0, not 0.0, not "/ 100".
    const heat = within(panel).getByText("Event Heat").closest("dl")!;
    expect(heat.textContent).not.toMatch(/\d/);
    expect(panel).not.toHaveTextContent("/ 100");
    // The score beside it is untouched.
    expect(screen.getByText("12.2")).toBeInTheDocument();
  });

  it("shows the radar as unavailable, which is not the same as empty", async () => {
    serve();
    render(<Radar />);
    const state = await screen.findByTestId("radar-unavailable");
    expect(state).toHaveTextContent("Radar unavailable");
    expect(state).toHaveTextContent("Nothing is known about upcoming events yet");
    expect(state).toHaveTextContent(radar().summary);
    expect(state).toHaveTextContent("no_event_coverage");
    expect(state).toHaveTextContent("none of the 15 games");
    expect(state).toHaveTextContent("This is not an empty radar");
    expect(screen.queryByTestId("radar-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^radar-window-/)).not.toBeInTheDocument();
    const note = screen.getByRole("complementary", { name: "What the radar is" });
    expect(note).toHaveTextContent("Timing and context, not recommendations");
    expect(note).toHaveTextContent(radar().scope_note);
    expect(screen.getByText("Games with event coverage").closest("div")).toHaveTextContent("0");
    expect(screen.getByText("Games without").closest("div")).toHaveTextContent("15");
  });

  it("renders the Event Heat rules from the methodology artifact", async () => {
    serve();
    render(<Methodology />);
    const rules = methodology().event_heat;
    const section = await screen.findByRole("region", { name: `Event Heat: ${rules.version}` });
    expect(within(section).getByRole("complementary", { name: "Status of Event Heat" })).toHaveTextContent(
      "Internal, uncalibrated, and not in score v0",
    );
    expect(section).toHaveTextContent(rules.scope_note);
    const types = within(section).getByRole("table", { name: "Event Heat rules by event type" });
    expect(within(types).getAllByRole("row")).toHaveLength(rules.types.length + 1);
    expect(rules.types).toHaveLength(15);
    for (const rule of rules.types) {
      expect(types).toHaveTextContent(rule.event_type);
      expect(types).toHaveTextContent(rule.rationale);
    }
    const parameters = within(section).getByRole("table", { name: "Event Heat parameters" });
    for (const parameter of rules.parameters) expect(parameters).toHaveTextContent(parameter.rationale);
    const availability = within(section).getByRole("table", { name: "Event Heat availability" });
    expect(availability).toHaveTextContent("Not knowing is never reported as zero.");
    for (const limitation of rules.limitations) expect(section).toHaveTextContent(limitation);
    // The score's own section still describes v0 and nothing else.
    expect(screen.getByRole("table", { name: "Score terms" })).toHaveTextContent("100 * (0.7 * anomaly + 0.3 * momentum)");
  });
});

describe("the event panel with events (synthetic)", () => {
  it("shows the heat, the strongest event, its phase, the timeline and the explanation", () => {
    render(<EventPanel events={AVAILABLE_WITH_EVENTS} />);
    const panel = screen.getByTestId("event-panel");
    expect(panel).toHaveAttribute("data-availability", "available");
    expect(within(panel).getByText("Event Heat").closest("div")).toHaveTextContent("46.9/ 100");
    const strongest = within(panel).getByText("Strongest event").closest("div")!;
    expect(strongest).toHaveTextContent("Synthetic Update");
    expect(strongest).toHaveTextContent("Major patch");
    expect(strongest).toHaveTextContent("Recent"); // the phase, in words
    expect(strongest).toHaveTextContent(SYNTHETIC_PATCH.text!);

    const rows = within(screen.getByTestId("event-timeline")).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual([
      expect.stringContaining("Synthetic Update"),
      expect.stringContaining("Synthetic Live"),
      expect.stringContaining("Synthetic Trailer"),
    ]);
    expect(rows[0]).toHaveTextContent("2 Oct 2026");
    expect(rows[0]).toHaveTextContent("0.71"); // strength
    expect(rows[0]).toHaveTextContent("0.42"); // contribution
    expect(rows[0]).toHaveTextContent("Yes, #1");
    expect(rows[1]).toHaveTextContent("Upcoming");
    expect(rows[1]).toHaveTextContent("Yes, #2");
    expect(rows[1]).toHaveTextContent("Showcase airs in 4 days: Synthetic Live.");
    expect(rows[2]).toHaveTextContent("No");
    expect(rows[2]).toHaveTextContent("Not yet in its ramp");
    expect(rows[2]).toHaveTextContent("0.00");
    expect(panel).toHaveTextContent("last looked at 3 Oct 2026, 00:00 UTC by Synthetic events");
    expect(panel).toHaveTextContent("Evaluated as of 4 Oct 2026, 00:00 UTC");
    expect(panel).toHaveTextContent("Internal and uncalibrated.");
  });

  it("shows a real zero as zero and says it is a finding", () => {
    render(<EventPanel events={AVAILABLE_WITH_NOTHING} />);
    const panel = screen.getByTestId("event-panel");
    expect(panel).toHaveAttribute("data-availability", "available");
    expect(within(panel).getByText("Event Heat").closest("div")).toHaveTextContent("0.0/ 100");
    expect(panel).toHaveTextContent("A source looked; nothing is relevant now.");
    expect(panel).toHaveTextContent("this is a finding, not a gap");
    expect(panel).not.toHaveTextContent("Unavailable");
    expect(screen.queryByTestId("event-timeline")).not.toBeInTheDocument();
  });

  it("never shows a tiny positive heat as zero", () => {
    render(<EventPanel events={AVAILABLE_TINY_POSITIVE} />);
    const panel = screen.getByTestId("event-panel");
    const heat = within(panel).getByText("Event Heat").closest("div")!;
    expect(heat).toHaveTextContent("<0.1/ 100");
    expect(heat).not.toHaveTextContent("0.0");
    // It is not described as a zero finding, and its event is shown as counted.
    expect(panel).not.toHaveTextContent("A source looked; nothing is relevant now.");
    expect(panel).toHaveTextContent("Internal and uncalibrated.");
    expect(within(panel).getByText("Strongest event").closest("div")).toHaveTextContent("Synthetic Edge Update");
    const row = within(screen.getByTestId("event-timeline")).getAllByRole("row")[1]!;
    expect(row).toHaveTextContent("Yes, #1");
    const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells.filter((text) => text === "<0.01")).toHaveLength(2); // strength and contribution
    expect(cells).not.toContain("0.00");
    // The three states read differently on screen.
    expect(eventHeatState(AVAILABLE_TINY_POSITIVE)).toEqual({ kind: "available", heat: 5.555555555e-5 });
    expect([formatHeat(5.555555555e-5), formatHeat(0), formatHeat(null)]).toEqual(["<0.1", "0.0", "—"]);
  });

  it("formats heat and shares without ever rounding a positive value to zero", () => {
    expect(formatHeat(0)).toBe("0.0");
    expect(formatHeat(1e-9)).toBe("<0.1");
    expect(formatHeat(0.0499)).toBe("<0.1");
    expect(formatHeat(0.05)).toBe("0.1");
    expect(formatHeat(0.1)).toBe("0.1");
    expect(formatHeat(46.864)).toBe("46.9");
    expect(formatHeat(100)).toBe("100.0");
    expect(formatHeat(undefined)).toBe("—");
    expect(formatShare(0)).toBe("0.00");
    expect(formatShare(5.5e-7)).toBe("<0.01");
    expect(formatShare(0.0049)).toBe("<0.01");
    expect(formatShare(0.005)).toBe("0.01");
    expect(formatShare(0.4243)).toBe("0.42");
    expect(formatShare(1)).toBe("1.00");
    expect(formatShare(null)).toBe("—");
  });

  it("shows stale coverage as unavailable, with when it was last looked at", () => {
    render(<EventPanel events={UNAVAILABLE_STALE} />);
    const panel = screen.getByTestId("event-panel");
    expect(panel).toHaveAttribute("data-availability", "unavailable");
    expect(panel).toHaveTextContent("event_coverage_stale");
    expect(panel).toHaveTextContent("too old to rely on");
    expect(panel).toHaveTextContent("Last looked at 1 Sep 2026, 00:00 UTC.");
    expect(within(panel).getByText("Event Heat").closest("dl")!.textContent).not.toMatch(/\d/);
  });

  it("never turns unavailable into a number, whatever the artifact's heat field holds", () => {
    expect(eventHeatState(UNAVAILABLE_STALE)).toEqual({ kind: "unavailable", reasons: ["event_coverage_stale"] });
    expect(eventHeatState(AVAILABLE_WITH_NOTHING)).toEqual({ kind: "available", heat: 0 });
    expect(eventHeatState(AVAILABLE_WITH_EVENTS)).toEqual({ kind: "available", heat: 46.9 });
    const withoutHeat: typeof UNAVAILABLE_STALE = { ...UNAVAILABLE_STALE };
    delete withoutHeat.event_heat;
    expect(eventHeatState(withoutHeat).kind).toBe("unavailable");
    expect(eventHeatState({ ...UNAVAILABLE_STALE, event_heat: 0 }).kind).toBe("unavailable");
  });

  it("describes when an event runs without inventing an end", () => {
    expect(eventTiming(SYNTHETIC_PATCH)).toBe("2 Oct 2026");
    expect(eventTiming(SYNTHETIC_EXPEDITION)).toBe("29 Sep 2026 to 3 Nov 2026");
    expect(eventTiming({ ...SYNTHETIC_EXPEDITION, end_assumed: true })).toBe("29 Sep 2026, end not stated");
    expect(eventTiming({ ...SYNTHETIC_TRAILER, starts_at: "2026-10-24T17:00:00Z", time_precision: "exact", ends_at: "2026-10-24T17:00:00Z" })).toBe(
      "24 Oct 2026, 17:00 UTC",
    );
  });

  it("renders on the game page when the artifact carries events", async () => {
    serve({
      [`${version}/games/minecraft.json`]: (body) => ({ ...(body as object), event_intelligence: AVAILABLE_WITH_EVENTS }),
    });
    render(<GameDetail slug="minecraft" />);
    await screen.findByRole("heading", { level: 1, name: "Minecraft" });
    const section = screen.getByRole("region", { name: "Event intelligence" });
    expect(within(section).getByTestId("event-panel")).toHaveAttribute("data-availability", "available");
    expect(section).toHaveTextContent("Separate from score v0.");
    expect(within(section).getByTestId("event-timeline")).toBeInTheDocument();
    // Score v0 on the same page is exactly the artifact's.
    expect(screen.getByText("12.2")).toBeInTheDocument();
    await waitFor(() => expect(chart.setOption).toHaveBeenCalled());
  });
});

describe("refusing event data that contradicts itself", () => {
  it.each([
    ["a heat on unavailable intelligence", { event_heat: 0 }, "/event_intelligence/event_heat"],
    ["availability without a heat", { availability: "available" }, "/event_intelligence/event_heat"],
    ["unavailable without a reason", { unavailable_reasons: [] }, "/event_intelligence/unavailable_reasons"],
  ])("rejects %s and renders nothing from it", async (_name, patch, path) => {
    const { fetcher } = bundleFetcher("/data", {
      [`${version}/games/minecraft.json`]: (body) => {
        const changed = body as { event_intelligence: object };
        changed.event_intelligence = { ...changed.event_intelligence, ...patch };
        return changed;
      },
    });
    await expect(loadGame("minecraft", { baseUrl: "/data", fetcher })).rejects.toMatchObject({
      kind: "invalid",
      details: expect.arrayContaining([expect.objectContaining({ path })]),
    });
    vi.stubGlobal("fetch", fetcher);
    render(<GameDetail slug="minecraft" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("An artifact does not match the contract");
    expect(screen.queryByTestId("event-panel")).not.toBeInTheDocument();
  });

  it("rejects a radar that is unavailable and lists events, or is missing", async () => {
    const withItems = bundleFetcher("/data", {
      [`${version}/radar.json`]: (body) => ({ ...(body as object), items: RADAR_ITEMS }),
    });
    await expect(loadRadar({ baseUrl: "/data", fetcher: withItems.fetcher })).rejects.toMatchObject({ kind: "invalid" });
    const missing = bundleFetcher("/data", { [`${version}/radar.json`]: null });
    await expect(loadRadar({ baseUrl: "/data", fetcher: missing.fetcher })).rejects.toMatchObject({ kind: "unavailable" });
    const badSchema = bundleFetcher("/data", {
      [`${version}/radar.json`]: (body) => ({ ...(body as object), availability: "maybe" }),
    });
    await expect(loadRadar({ baseUrl: "/data", fetcher: badSchema.fetcher })).rejects.toMatchObject({ kind: "invalid" });
  });

  it("rejects a game artifact with no event intelligence at all", async () => {
    const { fetcher } = bundleFetcher("/data", {
      [`${version}/games/minecraft.json`]: (body) => {
        const changed = body as Record<string, unknown>;
        delete changed.event_intelligence;
        return changed;
      },
    });
    await expect(loadGame("minecraft", { baseUrl: "/data", fetcher })).rejects.toMatchObject({ kind: "invalid" });
  });
});

describe("the radar with events (synthetic)", () => {
  it("groups upcoming events by window, soonest first, with timing and context", async () => {
    serve({ [`${version}/radar.json`]: () => availableRadar(radar()) });
    render(<Radar />);
    const day = await screen.findByTestId("radar-window-24");
    const week = screen.getByTestId("radar-window-168");
    const month = screen.getByTestId("radar-window-720");
    const titles = (list: HTMLElement) => within(list).getAllByRole("listitem").map((entry) => entry.textContent);
    expect(titles(day)).toEqual([expect.stringContaining("Synthetic Hotfix")]);
    expect(titles(week)).toEqual([expect.stringContaining("Synthetic Live")]);
    expect(titles(month)).toEqual([
      expect.stringContaining("Synthetic Launch"),
      expect.stringContaining("Synthetic Trailer"),
    ]);
    expect(screen.getByRole("heading", { name: /Next 24 hours/ })).toHaveTextContent("1");
    expect(screen.getByRole("heading", { name: /Next 7 days/ })).toHaveTextContent("1");
    expect(screen.getByRole("heading", { name: /Next 30 days/ })).toHaveTextContent("2");

    const hotfix = within(day).getByRole("listitem");
    expect(hotfix).toHaveTextContent("4 Oct 2026, 18:30 UTC");
    expect(hotfix).toHaveTextContent("in 19 hours");
    expect(within(hotfix).getByRole("link", { name: "No Man's Sky" })).toHaveAttribute("href", "/games/no-mans-sky");
    expect(hotfix).toHaveTextContent("Minor update launches later today: Synthetic Hotfix.");
    expect(hotfix).toHaveTextContent("In its ramp");

    const [launch, trailer] = within(month).getAllByRole("listitem");
    expect(launch).toHaveTextContent("16 Oct 2026");
    expect(launch).toHaveTextContent("in 12 days");
    expect(launch).toHaveTextContent("contribution 0.36 · game event heat 36.0");
    expect(trailer).toHaveTextContent("Scheduled");
    expect(trailer).toHaveTextContent("not yet adding to event heat");

    expect(screen.queryByTestId("radar-unavailable")).not.toBeInTheDocument();
    expect(screen.getByText("Games with event coverage").closest("div")).toHaveTextContent("5");
    // It lists events; it does not call any of them an opportunity or rank them.
    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/recommended|top pick|best opportunity|\brank\b/i);
  });

  it("shows an empty radar as a finding, distinct from unavailable", () => {
    render(<RadarBody radar={availableRadar(radar(), [])} />);
    const empty = screen.getByTestId("radar-empty");
    expect(empty).toHaveTextContent("No dated event starts within the horizon");
    expect(empty).toHaveTextContent("this is a finding, not a gap");
    expect(screen.queryByTestId("radar-unavailable")).not.toBeInTheDocument();
  });

  it("says so when a window holds nothing", () => {
    render(<RadarBody radar={availableRadar(radar(), RADAR_ITEMS.slice(2))} />);
    expect(screen.getAllByText("No dated event starts in this window.")).toHaveLength(2);
    expect(screen.getByTestId("radar-window-720")).toBeInTheDocument();
  });

  it("words distances and windows plainly", () => {
    expect(formatHoursAhead(0.4)).toBe("in under an hour");
    expect(formatHoursAhead(1)).toBe("in 1 hour");
    expect(formatHoursAhead(18.5)).toBe("in 19 hours");
    expect(formatHoursAhead(47.9)).toBe("in 48 hours");
    expect(formatHoursAhead(48)).toBe("in 2 days");
    expect(formatHoursAhead(288)).toBe("in 12 days");
    expect([24, 168, 720, 36].map(windowLabel)).toEqual(["Next 24 hours", "Next 7 days", "Next 30 days", "Next 36 hours"]);
  });
});

describe("navigation", () => {
  it("includes the radar among the private pages", () => {
    render(<SiteHeader />);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Overview",
      "Games",
      "Radar",
      "Sources",
      "Methodology",
    ]);
    expect(within(nav).getByRole("link", { name: "Radar" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Radar" })).toHaveAttribute("href", "/radar");
  });
});
