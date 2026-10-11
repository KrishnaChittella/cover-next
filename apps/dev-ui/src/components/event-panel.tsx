import type { EventIntelligenceView } from "@/contract";
import { eventHeatState, eventTiming, eventTypeLabel, phaseLabel, unavailableLabel } from "@/lib/events";
import { formatHeat, formatShare, formatTimestamp, humanize } from "@/lib/format";

import { Badge } from "./ui";

const PHASE_TONE: Record<string, "signal" | "good" | "info" | "neutral"> = {
  active: "good",
  upcoming: "info",
  decaying: "signal",
};

function Provenance({ events }: { events: EventIntelligenceView }) {
  return (
    <p className="mt-4 text-xs text-muted">
      Evaluated as of {formatTimestamp(events.as_of)} under{" "}
      <code className="font-mono text-ink-soft">{events.rules_version}</code> ({humanize(events.rules_status)}).
      Event Heat is reported beside score v0 and is not part of it.
    </p>
  );
}

/**
 * A game's event intelligence. Unavailable is shown as unavailable: there is no number,
 * no bar and no list, because nothing is known.
 */
export function EventPanel({ events }: { events: EventIntelligenceView }) {
  const state = eventHeatState(events);

  if (state.kind === "unavailable") {
    return (
      <div data-testid="event-panel" data-availability="unavailable" className="panel border-l-2 border-l-info p-5">
        <dl>
          <dt className="eyebrow">Event Heat</dt>
          <dd className="mt-1 text-xl font-medium text-muted">Unavailable</dd>
        </dl>
        <p className="mt-3 max-w-3xl text-sm text-ink-soft">{events.summary}</p>
        <ul className="mt-3 space-y-1 text-sm text-muted">
          {state.reasons.map((reason) => (
            <li key={reason} className="flex gap-3">
              <span className="font-mono text-xs">{reason}</span>
              <span>{unavailableLabel(reason)}</span>
            </li>
          ))}
        </ul>
        {events.coverage_checked_at ? (
          <p className="mt-3 text-xs text-muted">Last looked at {formatTimestamp(events.coverage_checked_at)}.</p>
        ) : null}
        <p className="mt-3 max-w-3xl text-xs text-muted">
          This is not a heat of zero. Zero would mean a source looked and found nothing relevant; here no number
          exists at all.
        </p>
        <Provenance events={events} />
      </div>
    );
  }

  const top = events.contributions.find((event) => event.event_id === events.top_event_id) ?? null;
  return (
    <div data-testid="event-panel" data-availability="available" className="panel p-5">
      <dl className="grid gap-x-8 gap-y-5 md:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]">
        <div>
          <dt className="eyebrow">Event Heat</dt>
          <dd className="num mt-1 font-mono text-3xl font-semibold text-ink">
            {formatHeat(state.heat)}
            <span className="ml-1 text-sm font-normal text-muted">/ 100</span>
          </dd>
          <dd className="mt-1.5 text-xs text-muted">
            {state.heat === 0 ? "A source looked; nothing is relevant now." : "Internal and uncalibrated."}
          </dd>
        </div>
        <div>
          <dt className="eyebrow">{top ? "Strongest event" : "Finding"}</dt>
          {top ? (
            <>
              <dd className="mt-1 flex flex-wrap items-center gap-2 text-base text-ink">
                <span className="font-medium">{top.title}</span>
                <Badge>{eventTypeLabel(top.event_type)}</Badge>
                <Badge tone={PHASE_TONE[top.phase] ?? "neutral"} glyph>
                  {phaseLabel(top.phase)}
                </Badge>
              </dd>
              <dd className="mt-1.5 max-w-3xl text-sm text-ink-soft">{events.summary}</dd>
            </>
          ) : (
            <dd className="mt-1 max-w-3xl text-sm text-ink-soft">{events.summary}</dd>
          )}
        </div>
      </dl>

      {events.contributions.length > 0 ? (
        <div className="mt-5 overflow-x-auto border-t border-line pt-4">
          <table data-testid="event-timeline" className="w-full border-collapse text-sm">
            <caption className="mb-2 text-left text-xs text-muted">
              Relevant events, counted ones first. Contribution is importance times strength, each 0 to 1.
            </caption>
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th scope="col" className="py-2 pr-4 text-left font-medium">Event</th>
                <th scope="col" className="px-4 py-2 text-left font-medium">When (UTC)</th>
                <th scope="col" className="px-4 py-2 text-left font-medium">Phase</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Strength</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Contribution</th>
                <th scope="col" className="py-2 pl-4 text-left font-medium">Counted</th>
              </tr>
            </thead>
            <tbody>
              {events.contributions.map((event) => (
                <tr key={event.event_id} className="border-b border-line align-top last:border-b-0">
                  <th scope="row" className="py-3 pr-4 text-left font-normal">
                    <span className="font-medium text-ink">{event.title}</span>
                    <span className="mt-0.5 block text-xs text-muted">
                      {eventTypeLabel(event.event_type)} · {event.sources.map(humanize).join(", ")}
                    </span>
                    {event.text ? <span className="mt-1 block max-w-md text-xs text-ink-soft">{event.text}</span> : null}
                  </th>
                  <td className="num px-4 py-3 whitespace-nowrap text-ink-soft">{eventTiming(event)}</td>
                  <td className="px-4 py-3">
                    <Badge tone={PHASE_TONE[event.phase] ?? "neutral"} glyph>
                      {phaseLabel(event.phase)}
                    </Badge>
                  </td>
                  <td className="num px-4 py-3 text-right font-mono">{formatShare(event.strength)}</td>
                  <td className="num px-4 py-3 text-right font-mono">{formatShare(event.contribution)}</td>
                  <td className="py-3 pl-4 text-xs text-ink-soft">
                    {event.counted ? (
                      <span className="num">Yes, #{event.rank}</span>
                    ) : (
                      <>
                        No
                        {event.not_counted_because ? (
                          <span className="block text-muted">{humanize(event.not_counted_because)}</span>
                        ) : event.phase === "upcoming" ? (
                          <span className="block text-muted">Not yet in its ramp</span>
                        ) : null}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <p className="mt-4 text-xs text-muted">
        Coverage: last looked at {formatTimestamp(events.coverage_checked_at)}
        {events.coverage_sources.length > 0 ? ` by ${events.coverage_sources.map(humanize).join(", ")}` : ""}.
      </p>
      <Provenance events={events} />
    </div>
  );
}
