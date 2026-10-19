// SYNTHETIC event data, for tests only. None of this is real and none of it is ever
// served to the developer UI: the real replay bundle has no event coverage at all.
//
// The values mirror what the Python engine produces for the same invented events (see
// tests/test_event_artifacts.py), and every object here is checked against the
// checked-in schema by the tests that use it.
import type { EventContributionView, EventIntelligenceView, RadarArtifact, RadarItemView } from "@/contract";

const AS_OF = "2026-10-04T00:00:00Z";

function contribution(overrides: Partial<EventContributionView> & Pick<EventContributionView, "event_id" | "title">): EventContributionView {
  return {
    event_type: "major_patch",
    starts_at: "2026-10-02T00:00:00Z",
    ends_at: "2026-10-02T00:00:00Z",
    end_assumed: true,
    time_precision: "day",
    status: "scheduled",
    phase: "decaying",
    in_ramp: false,
    strength: 0.7071,
    importance: 0.6,
    contribution: 0.4243,
    counted: true,
    rank: 1,
    not_counted_because: null,
    sources: ["synthetic_events"],
    text: null,
    ...overrides,
  };
}

export const SYNTHETIC_PATCH = contribution({
  event_id: "evt_000000000000000000000001",
  title: "Synthetic Update",
  text: "Major update launched 2 days ago; event influence is still elevated: Synthetic Update.",
});

export const SYNTHETIC_SHOWCASE = contribution({
  event_id: "evt_000000000000000000000002",
  title: "Synthetic Live",
  event_type: "showcase",
  starts_at: "2026-10-08T00:00:00Z",
  ends_at: "2026-10-09T00:00:00Z",
  phase: "upcoming",
  in_ramp: true,
  strength: 0.1714,
  importance: 0.45,
  contribution: 0.0771,
  rank: 2,
  text: "Showcase airs in 4 days: Synthetic Live.",
});

export const SYNTHETIC_TRAILER = contribution({
  event_id: "evt_000000000000000000000003",
  title: "Synthetic Trailer",
  event_type: "trailer",
  starts_at: "2026-10-24T00:00:00Z",
  ends_at: "2026-10-24T00:00:00Z",
  phase: "upcoming",
  in_ramp: false,
  strength: 0,
  importance: 0.4,
  contribution: 0,
  counted: false,
  rank: null,
  text: "Trailer premieres in 20 days: Synthetic Trailer. It is not yet adding to event heat.",
});

export const SYNTHETIC_EXPEDITION = contribution({
  event_id: "evt_000000000000000000000004",
  title: "Synthetic Expedition",
  event_type: "expedition",
  starts_at: "2026-09-29T00:00:00Z",
  ends_at: "2026-11-04T00:00:00Z",
  end_assumed: false,
  phase: "active",
  strength: 0.5,
  importance: 0.45,
  contribution: 0.225,
  text: "Expedition started 5 days ago and is still running: Synthetic Expedition. It ends in 31 days.",
});

const base = {
  rules_version: "event_heat_rules_v1",
  rules_status: "internal_uncalibrated",
  as_of: AS_OF,
};

/** Available, with events and a heat above zero. */
export const AVAILABLE_WITH_EVENTS: EventIntelligenceView = {
  ...base,
  availability: "available",
  event_heat: 46.9,
  top_event_id: SYNTHETIC_PATCH.event_id,
  summary: SYNTHETIC_PATCH.text ?? "",
  reasons: [
    { code: "event_recent", text: SYNTHETIC_PATCH.text ?? "" },
    { code: "event_upcoming", text: SYNTHETIC_SHOWCASE.text ?? "" },
    { code: "event_scheduled", text: SYNTHETIC_TRAILER.text ?? "" },
  ],
  unavailable_reasons: [],
  coverage_checked_at: "2026-10-03T00:00:00Z",
  coverage_sources: ["synthetic_events"],
  contributions: [SYNTHETIC_PATCH, SYNTHETIC_SHOWCASE, SYNTHETIC_TRAILER],
};

/** One second inside a five-day ramp: a real, positive heat far below display precision. */
export const SYNTHETIC_EDGE = contribution({
  event_id: "evt_000000000000000000000007",
  title: "Synthetic Edge Update",
  starts_at: "2026-10-08T23:59:59Z",
  ends_at: "2026-10-08T23:59:59Z",
  time_precision: "exact",
  phase: "upcoming",
  in_ramp: true,
  strength: 9.259259259e-7,
  importance: 0.6,
  contribution: 5.555555555e-7,
  text: "Major update launches in 4 days: Synthetic Edge Update.",
});

export const AVAILABLE_TINY_POSITIVE: EventIntelligenceView = {
  ...base,
  availability: "available",
  event_heat: 5.555555555e-5,
  top_event_id: SYNTHETIC_EDGE.event_id,
  summary: SYNTHETIC_EDGE.text ?? "",
  reasons: [{ code: "event_upcoming", text: SYNTHETIC_EDGE.text ?? "" }],
  unavailable_reasons: [],
  coverage_checked_at: "2026-10-03T00:00:00Z",
  coverage_sources: ["synthetic_events"],
  contributions: [SYNTHETIC_EDGE],
};

/** Available, and a source found nothing relevant: a real zero. */
export const AVAILABLE_WITH_NOTHING: EventIntelligenceView = {
  ...base,
  availability: "available",
  event_heat: 0,
  top_event_id: null,
  summary:
    "No event is upcoming, running or recent for this game. Event coverage is current, so this is a finding, not a gap.",
  reasons: [
    {
      code: "no_relevant_event",
      text: "No event is upcoming, running or recent for this game. Event coverage is current, so this is a finding, not a gap.",
    },
  ],
  unavailable_reasons: [],
  coverage_checked_at: "2026-10-03T00:00:00Z",
  coverage_sources: ["synthetic_events"],
  contributions: [],
};

/** Looked at too long ago to rely on. */
export const UNAVAILABLE_STALE: EventIntelligenceView = {
  ...base,
  availability: "unavailable",
  event_heat: null,
  top_event_id: null,
  summary:
    "Event intelligence is unavailable: the last look at this game's events is too old to rely on. That is not the same as no events.",
  reasons: [
    {
      code: "event_coverage_stale",
      text: "Event intelligence is unavailable: the last look at this game's events is too old to rely on. That is not the same as no events.",
    },
  ],
  unavailable_reasons: ["event_coverage_stale"],
  coverage_checked_at: "2026-09-01T00:00:00Z",
  coverage_sources: ["synthetic_events"],
  contributions: [],
};

function item(overrides: Partial<RadarItemView> & Pick<RadarItemView, "event_id" | "slug" | "name" | "title">): RadarItemView {
  return {
    canonical_game_id: "d1c9712e-1691-49c0-be37-a3ea0dd06760",
    event_type: "showcase",
    starts_at: "2026-10-08T00:00:00Z",
    time_precision: "day",
    hours_until_start: 96,
    window_hours: 168,
    in_ramp: true,
    strength: 0.1714,
    contribution: 0.0771,
    game_event_heat: 46.9,
    sources: ["synthetic_events"],
    text: "Showcase airs in 4 days: Synthetic Live.",
    artifact_path: `games/${overrides.slug}.json`,
    ...overrides,
  };
}

export const RADAR_ITEMS: RadarItemView[] = [
  item({
    event_id: "evt_000000000000000000000005",
    slug: "no-mans-sky",
    name: "No Man's Sky",
    title: "Synthetic Hotfix",
    event_type: "minor_patch",
    starts_at: "2026-10-04T18:30:00Z",
    time_precision: "exact",
    hours_until_start: 18.5,
    window_hours: 24,
    strength: 0.0458,
    contribution: 0.0069,
    game_event_heat: 23.0,
    text: "Minor update launches later today: Synthetic Hotfix.",
  }),
  item({ event_id: SYNTHETIC_SHOWCASE.event_id, slug: "minecraft", name: "Minecraft", title: "Synthetic Live" }),
  item({
    event_id: "evt_000000000000000000000006",
    slug: "gta-vi",
    name: "Grand Theft Auto VI",
    title: "Synthetic Launch",
    event_type: "release",
    starts_at: "2026-10-16T00:00:00Z",
    hours_until_start: 288,
    window_hours: 720,
    strength: 0.36,
    contribution: 0.36,
    game_event_heat: 36.0,
    text: "Release launches in 12 days: Synthetic Launch.",
  }),
  item({
    event_id: SYNTHETIC_TRAILER.event_id,
    slug: "minecraft",
    name: "Minecraft",
    title: "Synthetic Trailer",
    event_type: "trailer",
    starts_at: "2026-10-24T00:00:00Z",
    hours_until_start: 480,
    window_hours: 720,
    in_ramp: false,
    strength: 0,
    contribution: 0,
    text: "Trailer premieres in 20 days: Synthetic Trailer. It is not yet adding to event heat.",
  }),
];

/** A radar with coverage: `items` empty gives the available-but-empty state. */
export function availableRadar(real: RadarArtifact, items: RadarItemView[] = RADAR_ITEMS): RadarArtifact {
  return {
    ...real,
    availability: "available",
    unavailable_reasons: [],
    games_with_coverage: 5,
    games_without_coverage: 10,
    summary:
      items.length === 0
        ? "No dated event starts within the horizon for any game with event coverage."
        : `${items.length} dated events start within the horizon.`,
    items,
  };
}
