"use client";

import Link from "next/link";

import type { RadarArtifact, RadarItemView } from "@/contract";
import { loadRadar } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";
import { eventTypeLabel, formatEventMoment, formatHoursAhead, unavailableLabel, windowLabel } from "@/lib/events";
import { formatCount, formatScore, formatTimestamp, formatUnit, humanize } from "@/lib/format";

import { DataState } from "./data-state";
import { RunNote } from "./run-note";
import { Badge, Stat } from "./ui";

function ScopeNote({ radar }: { radar: RadarArtifact }) {
  return (
    <aside aria-label="What the radar is" className="panel border-l-2 border-l-signal px-4 py-3">
      <p className="eyebrow text-signal">Timing and context, not recommendations</p>
      <p className="mt-1.5 max-w-4xl text-sm text-ink-soft">{radar.scope_note}</p>
      <p className="mt-1.5 max-w-4xl text-sm text-muted">
        Evaluated as of {formatTimestamp(radar.as_of)} under{" "}
        <code className="font-mono text-ink-soft">{radar.rules_version}</code> ({humanize(radar.rules_status)}).
      </p>
    </aside>
  );
}

function Coverage({ radar }: { radar: RadarArtifact }) {
  return (
    <dl className="panel mt-4 grid grid-cols-2 gap-x-6 gap-y-5 p-5 md:grid-cols-4">
      <Stat label="Radar">{radar.availability === "available" ? "Available" : "Unavailable"}</Stat>
      <Stat label="Games with event coverage">
        <span className="num">{formatCount(radar.games_with_coverage)}</span>
      </Stat>
      <Stat label="Games without" hint="Nothing is known about their events">
        <span className="num">{formatCount(radar.games_without_coverage)}</span>
      </Stat>
      <Stat label="Windows">{radar.windows_hours.map(windowLabel).join(" · ")}</Stat>
    </dl>
  );
}

function Item({ item }: { item: RadarItemView }) {
  return (
    <li className="grid gap-x-6 gap-y-2 px-4 py-3 md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <p className="num text-sm text-ink">{formatEventMoment(item.starts_at, item.time_precision)}</p>
        <p className="num mt-0.5 text-xs text-muted">{formatHoursAhead(item.hours_until_start)}</p>
      </div>
      <div className="min-w-0">
        <p className="text-sm">
          <Link
            href={`/games/${item.slug}`}
            className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
          >
            {item.name}
          </Link>
          <span className="ml-2 text-ink-soft">{item.title}</span>
        </p>
        <p className="mt-1 text-sm text-ink-soft">{item.text}</p>
        <p className="mt-1 text-xs text-muted">Reported by {item.sources.map(humanize).join(", ")}</p>
      </div>
      <div className="flex flex-wrap items-start gap-2 md:justify-end">
        <Badge>{eventTypeLabel(item.event_type)}</Badge>
        <Badge tone={item.in_ramp ? "signal" : "neutral"}>{item.in_ramp ? "In its ramp" : "Scheduled"}</Badge>
        <span className="num w-full text-xs text-muted md:text-right">
          contribution {formatUnit(item.contribution)} · game event heat {formatScore(item.game_event_heat)}
        </span>
      </div>
    </li>
  );
}

export function RadarBody({ radar }: { radar: RadarArtifact }) {
  if (radar.availability === "unavailable") {
    return (
      <>
        <ScopeNote radar={radar} />
        <div data-testid="radar-unavailable" className="panel mt-4 border-l-2 border-l-info p-6">
          <p className="eyebrow text-info">Radar unavailable</p>
          <h2 className="mt-2 text-lg font-semibold text-ink">Nothing is known about upcoming events yet</h2>
          <p className="mt-2 max-w-3xl text-sm text-ink-soft">{radar.summary}</p>
          <ul className="mt-3 space-y-1 text-sm text-muted">
            {radar.unavailable_reasons.map((reason) => (
              <li key={reason} className="flex gap-3">
                <span className="font-mono text-xs">{reason}</span>
                <span>{unavailableLabel(reason).replace("this game's", "any game's")}</span>
              </li>
            ))}
          </ul>
          <p className="mt-4 max-w-3xl text-sm text-muted">
            No event source is collected yet, so none of the {formatCount(radar.games_without_coverage)} games in
            this run has event coverage. This is not an empty radar: an empty radar would mean a source looked and
            found nothing scheduled.
          </p>
        </div>
        <Coverage radar={radar} />
      </>
    );
  }
  return (
    <>
      <ScopeNote radar={radar} />
      <Coverage radar={radar} />
      {radar.items.length === 0 ? (
        <p data-testid="radar-empty" className="panel mt-4 p-5 text-sm text-ink-soft">
          {radar.summary} Event coverage is current for {formatCount(radar.games_with_coverage)} game
          {radar.games_with_coverage === 1 ? "" : "s"}, so this is a finding, not a gap.
        </p>
      ) : (
        radar.windows_hours.map((window) => {
          const items = radar.items.filter((item) => item.window_hours === window);
          return (
            <section key={window} aria-labelledby={`radar-${window}`} className="mt-8">
              <h2 id={`radar-${window}`} className="mb-3 text-base font-semibold tracking-tight text-ink">
                {windowLabel(window)}
                <span className="num ml-2 text-sm font-normal text-muted">{items.length}</span>
              </h2>
              {items.length === 0 ? (
                <p className="panel p-4 text-sm text-muted">No dated event starts in this window.</p>
              ) : (
                <ol data-testid={`radar-window-${window}`} className="panel divide-y divide-line">
                  {items.map((item) => (
                    <Item key={item.event_id} item={item} />
                  ))}
                </ol>
              )}
            </section>
          );
        })
      )}
    </>
  );
}

export function Radar() {
  const state = useArtifact("radar", loadRadar);
  return (
    <DataState state={state} label="the radar" rows={8}>
      {({ manifest, data }) => (
        <>
          <RadarBody radar={data} />
          <RunNote manifest={manifest} />
        </>
      )}
    </DataState>
  );
}
