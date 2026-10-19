// Reading event intelligence. The one rule: unavailable is its own state. It is never
// turned into a number, and a number is never shown for it.
import type { EventContributionView, EventIntelligenceView } from "@/contract";

import { formatDay, humanize } from "./format";

export type EventHeatState =
  | { kind: "unavailable"; reasons: readonly string[] }
  | { kind: "available"; heat: number };

/** The heat as a state. Unavailable carries no number at all. */
export function eventHeatState(events: EventIntelligenceView): EventHeatState {
  const heat = events.event_heat;
  if (events.availability !== "available" || heat === null || heat === undefined) {
    return { kind: "unavailable", reasons: events.unavailable_reasons };
  }
  return { kind: "available", heat };
}

const UNAVAILABLE: Record<string, string> = {
  no_event_coverage: "No source has looked for this game's events.",
  event_coverage_stale: "The last look at this game's events is too old to rely on.",
};

export const unavailableLabel = (reason: string): string => UNAVAILABLE[reason] ?? humanize(reason);

const PHASE: Record<string, string> = {
  upcoming: "Upcoming",
  active: "Running",
  decaying: "Recent",
  expired: "Expired",
  called_off: "Called off",
};

export const phaseLabel = (phase: string): string => PHASE[phase] ?? humanize(phase);

const TYPE: Record<string, string> = { dlc: "DLC", major_in_game_event: "In-game event" };

export const eventTypeLabel = (type: string): string => TYPE[type] ?? humanize(type);

/** A contract timestamp at the precision the source gave: a day, or a UTC minute. */
export function formatEventMoment(timestamp: string, precision: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  const iso = date.toISOString();
  const day = formatDay(iso.slice(0, 10));
  return precision === "exact" ? `${day}, ${iso.slice(11, 16)} UTC` : day;
}

/** When an event runs. An assumed end is said to be assumed, never shown as a date. */
export function eventTiming(event: EventContributionView): string {
  const start = formatEventMoment(event.starts_at, event.time_precision);
  if (event.end_assumed) return event.ends_at === event.starts_at ? start : `${start}, end not stated`;
  if (event.time_precision === "exact") return `${start} to ${formatEventMoment(event.ends_at, "exact")}`;
  // A day-precision end is the first moment after the last day.
  const last = new Date(new Date(event.ends_at).getTime() - 1).toISOString().slice(0, 10);
  const lastDay = formatDay(last);
  return lastDay === start ? start : `${start} to ${lastDay}`;
}

/** How far off an upcoming event is, in the largest unit that reads naturally. */
export function formatHoursAhead(hours: number): string {
  if (hours < 1) return "in under an hour";
  if (hours < 48) {
    const whole = Math.round(hours);
    return `in ${whole} hour${whole === 1 ? "" : "s"}`;
  }
  const days = Math.round(hours / 24);
  return `in ${days} days`;
}

export function windowLabel(hours: number): string {
  if (hours === 24) return "Next 24 hours";
  if (hours % 24 === 0) return `Next ${hours / 24} days`;
  return `Next ${hours} hours`;
}
