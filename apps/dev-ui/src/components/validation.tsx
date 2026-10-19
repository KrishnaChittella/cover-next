"use client";

import Link from "next/link";
import { useId, useState } from "react";

import type {
  InspectionDayView,
  OutcomeSummaryView,
  StrategyResultView,
  StratumView,
  ValidationArtifact,
  ValidationReportView,
} from "@/contract";
import { loadValidation } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";
import {
  ABSENT,
  formatConfidence,
  formatCorrelation,
  formatCount,
  formatDay,
  formatDecimal,
  formatOutcome,
  formatRate,
  formatScore,
  formatTimestamp,
  humanize,
} from "@/lib/format";

import { DataState } from "./data-state";
import { RunNote } from "./run-note";
import { Badge, Section, Stat } from "./ui";

const STATUS_TONE: Record<string, "bad" | "warn" | "info" | "good"> = {
  framework_only: "bad",
  insufficient_sample: "warn",
  exploratory: "info",
  validated: "good",
};

const STATUS_MEANING: Record<string, string> = {
  framework_only: "The machinery works. Nothing is shown about the score.",
  insufficient_sample: "Every metric computes. None supports a conclusion.",
  exploratory: "Enough to look for patterns. Confirms nothing.",
  validated: "Confirmed on a held-out period.",
};

function Outcome({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) {
    return (
      <span className="text-faint">
        <span aria-hidden="true">{ABSENT}</span>
        <span className="sr-only">not available</span>
      </span>
    );
  }
  const tone = value > 0 ? "text-up" : value < 0 ? "text-down" : "text-ink-soft";
  return <span className={tone}>{formatOutcome(value)}</span>;
}

function SummaryCells({ summary }: { summary: OutcomeSummaryView }) {
  return (
    <>
      <td className="num px-3 py-2.5 text-right font-mono">{formatCount(summary.count)}</td>
      <td className="num px-3 py-2.5 text-right font-mono">
        <Outcome value={summary.mean} />
      </td>
      <td className="num px-3 py-2.5 text-right font-mono">
        <Outcome value={summary.median} />
      </td>
      <td className="num px-3 py-2.5 text-right font-mono">{formatRate(summary.hit_rate)}</td>
      <td className="num px-3 py-2.5 text-right font-mono">{formatRate(summary.downside_rate)}</td>
    </>
  );
}

const SUMMARY_HEADERS = ["n", "Mean", "Median", "Hit rate", "Downside rate"];

function SummaryHead({ first }: { first: string }) {
  return (
    <thead>
      <tr className="border-b border-line text-xs text-muted">
        <th scope="col" className="px-4 py-2 text-left font-medium">{first}</th>
        {SUMMARY_HEADERS.map((header) => (
          <th key={header} scope="col" className="px-3 py-2 text-right font-medium">{header}</th>
        ))}
      </tr>
    </thead>
  );
}

function Strata({ strata, caption, first }: { strata: readonly StratumView[]; caption: string; first: string }) {
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <SummaryHead first={first} />
        <tbody>
          {strata.map((stratum) => (
            <tr key={stratum.label} className="border-b border-line last:border-b-0">
              <th scope="row" className="px-4 py-2.5 text-left font-normal text-ink">{humanize(stratum.label)}</th>
              <SummaryCells summary={stratum.outcomes} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Strategies({ strategies, scoreVersion }: { strategies: readonly StrategyResultView[]; scoreVersion: string }) {
  return (
    <div className="panel overflow-x-auto">
      <table data-testid="strategy-table" className="w-full border-collapse text-sm">
        <caption className="sr-only">Primary outcome by strategy and leading group</caption>
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th scope="col" className="px-4 py-2 text-left font-medium">Strategy</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Group</th>
            {SUMMARY_HEADERS.map((header) => (
              <th key={header} scope="col" className="px-3 py-2 text-right font-medium">{header}</th>
            ))}
            <th scope="col" className="px-3 py-2 text-right font-medium">Rank association</th>
          </tr>
        </thead>
        {strategies.map((strategy) => (
          <tbody key={strategy.strategy} data-strategy={strategy.strategy} className="border-b border-line last:border-b-0">
            {strategy.top_k.map((group, index) => (
              <tr key={group.k} className="align-top">
                {index === 0 ? (
                  <th scope="rowgroup" rowSpan={strategy.top_k.length} className="max-w-56 px-4 py-2.5 text-left font-normal">
                    <span className="font-medium text-ink">{strategy.label}</span>
                    {strategy.strategy === scoreVersion ? (
                      <span className="ml-2 align-middle">
                        <Badge tone="signal">under test</Badge>
                      </span>
                    ) : null}
                    <span className="mt-1 block text-xs text-muted">{strategy.description}</span>
                  </th>
                ) : null}
                <th scope="row" className="num px-3 py-2.5 text-left font-normal whitespace-nowrap text-ink-soft">
                  Top {group.k}
                </th>
                <SummaryCells summary={group.outcomes} />
                {index === 0 ? (
                  <td rowSpan={strategy.top_k.length} className="num px-3 py-2.5 text-right font-mono">
                    {formatCorrelation(strategy.association.mean_rho)}
                    <span className="mt-1 block font-sans text-xs text-muted">
                      {formatCount(strategy.association.days_used)} days; {formatCount(strategy.association.positive_days)}{" "}
                      positive, {formatCount(strategy.association.negative_days)} negative
                    </span>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

function Inspection({ days }: { days: readonly InspectionDayView[] }) {
  const id = useId();
  const [selected, setSelected] = useState(days.length > 0 ? days[days.length - 1]!.day : "");
  const day = days.find((entry) => entry.day === selected) ?? null;
  if (days.length === 0 || day === null) {
    return <p className="panel p-5 text-sm text-ink-soft">The report keeps no days for inspection.</p>;
  }
  return (
    <div>
      <div className="mb-3">
        <label htmlFor={`${id}-day`} className="eyebrow mb-1 block">
          Recommendation day
        </label>
        <select
          id={`${id}-day`}
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
          className="h-9 rounded border border-line-strong bg-raised px-2.5 text-sm text-ink hover:border-muted"
        >
          {[...days].reverse().map((entry) => (
            <option key={entry.day} value={entry.day}>
              {formatDay(entry.day)}
            </option>
          ))}
        </select>
      </div>
      <dl data-testid="inspection-timing" className="panel mb-3 grid gap-x-6 gap-y-4 p-4 sm:grid-cols-3">
        <Stat label="Recommendation simulated at">
          <span className="num">{formatTimestamp(day.as_of)}</span>
        </Stat>
        <Stat label="Newest demand day visible then" hint="Later days had not been published by the source.">
          <span className="num">{formatDay(day.latest_due_day)}</span>
        </Stat>
        <Stat label="Judged on the next 72 hours" hint="Whole UTC days starting at or after the recommendation.">
          <span className="num">
            {formatDay(day.primary_window_start)} to {formatDay(day.primary_window_end)}
          </span>
        </Stat>
      </dl>
      <div className="panel overflow-x-auto">
        <table data-testid="inspection-table" className="w-full border-collapse text-sm">
          <caption className="sr-only">Ranking made on {formatDay(day.day)} and what followed</caption>
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th scope="col" className="px-4 py-2 text-right font-medium">Rank</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Game</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Score v0</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Confidence</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Next 72h vs baseline</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Next 24h</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Week above baseline</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Days to peak</th>
            </tr>
          </thead>
          <tbody>
            {day.ranked.map((entry) => (
              <tr key={entry.slug} className="border-b border-line last:border-b-0">
                <td className="num px-4 py-2.5 text-right font-mono text-muted">{entry.rank}</td>
                <th scope="row" className="px-3 py-2.5 text-left font-normal">
                  <Link
                    href={`/games/${entry.slug}`}
                    className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal"
                  >
                    {entry.name}
                  </Link>
                </th>
                <td className="num px-3 py-2.5 text-right font-mono">{formatScore(entry.score)}</td>
                <td className="num px-3 py-2.5 text-right font-mono">{formatConfidence(entry.confidence)}</td>
                <td className="num px-3 py-2.5 text-right font-mono">
                  {entry.outcome_availability === "available" ? (
                    <Outcome value={entry.realized_uplift_72h} />
                  ) : (
                    <span className="font-sans text-xs text-muted">{humanize(entry.unavailable_reason ?? "unavailable")}</span>
                  )}
                </td>
                <td className="num px-3 py-2.5 text-right font-mono">
                  <Outcome value={entry.movement_24h} />
                </td>
                <td className="num px-3 py-2.5 text-right font-mono">{formatRate(entry.persistence_7d)}</td>
                <td className="num px-3 py-2.5 text-right font-mono">
                  {entry.lead_time_days === null || entry.lead_time_days === undefined ? ABSENT : entry.lead_time_days}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {day.unranked.length > 0 ? (
        <p className="mt-2 text-xs text-muted">
          Unranked that day ({day.unranked.length}): {day.unranked.map((entry) => entry.name).join(", ")}.
        </p>
      ) : null}
    </div>
  );
}

function OutcomeDefinition({ validation }: { validation: ValidationArtifact }) {
  const definition = validation.outcome_definition;
  return (
    <Section
      id="outcomes"
      title={`Outcome definition: ${definition.version}`}
      note="What 'what happened afterwards' means. Fixed before any backtest was run; every reference level is the one known at the snapshot."
    >
      <div className="panel overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Outcome definitions</caption>
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th scope="col" className="px-4 py-2 text-left font-medium">Outcome</th>
              <th scope="col" className="px-4 py-2 text-left font-medium">Formula</th>
              <th scope="col" className="px-4 py-2 text-left font-medium">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {[...definition.terms, ...definition.unavailable].map((term) => (
              <tr key={term.name} className="border-b border-line align-top last:border-b-0">
                <th scope="row" className="px-4 py-3 text-left font-mono text-xs font-medium text-ink">{term.name}</th>
                <td className="px-4 py-3 font-mono text-xs text-signal">{term.formula}</td>
                <td className="max-w-xl px-4 py-3 text-ink-soft">{term.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="panel mt-4 grid gap-x-8 gap-y-4 p-5 md:grid-cols-2">
        {definition.parameters.map((parameter) => (
          <Stat key={parameter.name} label={`${humanize(parameter.name)}: ${parameter.value}`}>
            <span className="text-ink-soft">{parameter.rationale}</span>
          </Stat>
        ))}
      </dl>
    </Section>
  );
}

function Report({ validation, report }: { validation: ValidationArtifact; report: ValidationReportView }) {
  const status = report.evidence.status;
  const unavailable = Object.entries(report.primary_unavailable);
  const lags = Object.entries(report.publication_lag_hours);
  return (
    <>
      <aside
        data-testid="evidence-status"
        data-status={status}
        aria-label="Evidence status"
        className="panel border-l-2 border-l-bad px-5 py-4"
      >
        <div className="flex flex-wrap items-center gap-3">
          <p className="eyebrow">Evidence status</p>
          <Badge tone={STATUS_TONE[status] ?? "warn"} glyph>
            {humanize(status)}
          </Badge>
          <span className="text-sm text-ink-soft">{STATUS_MEANING[status] ?? ""}</span>
        </div>
        <p className="mt-2 max-w-4xl text-sm text-ink">{report.evidence.conclusion}</p>
        <p className="mt-2 max-w-4xl text-sm text-muted">{validation.scope_note}</p>
      </aside>

      <Section id="tested" title="What was tested">
        <dl className="panel grid grid-cols-2 gap-x-6 gap-y-5 p-5 md:grid-cols-4">
          <Stat
            label="Recommendation days"
            hint={`${formatDay(report.first_prediction_day)} to ${formatDay(report.last_prediction_day)}`}
          >
            <span className="num">{formatCount(report.snapshot_dates)}</span>
          </Stat>
          <Stat label="Games ranked at least once" hint={`of ${formatCount(report.universe_size)} in the universe`}>
            <span className="num">{formatCount(report.games_ranked)}</span>
          </Stat>
          <Stat label="Snapshots" hint={`${formatCount(report.ranked)} ranked, ${formatCount(report.unranked)} unranked`}>
            <span className="num">{formatCount(report.recommendations)}</span>
          </Stat>
          <Stat
            label="Outcomes evaluable"
            hint={unavailable.map(([reason, count]) => `${formatCount(count)} ${humanize(reason).toLowerCase()}`).join("; ")}
          >
            <span className="num">{formatCount(report.primary_evaluable)}</span>
          </Stat>
          <Stat label="Recording ends">
            <span className="num">{formatDay(report.data_through)}</span>
          </Stat>
          <Stat label="Independent sources">
            <span className="num">{formatCount(report.independent_sources)}</span>
          </Stat>
          <Stat label="Protocol">
            <span className="font-mono text-xs">{report.protocol_version}</span>
          </Stat>
          <Stat label="Score under test">
            <span className="font-mono text-xs">{report.score_version}</span>
          </Stat>
        </dl>
      </Section>

      <Section
        id="timing"
        title="What each recommendation could see"
        note="A day of demand counts as visible only once its source would have published it. A day that had ended but was not yet published is left out of the recommendation and is not part of its outcome either."
      >
        <dl data-testid="timing" className="panel grid gap-x-6 gap-y-5 p-5 md:grid-cols-3">
          <Stat label="Newest demand day visible" hint="At the first and the last recommendation.">
            <span className="num">
              {formatDay(report.first_latest_due_day)} to {formatDay(report.last_latest_due_day)}
            </span>
          </Stat>
          <Stat label="Source publication lag" hint="After a day ends, before it counts as published.">
            <span className="num">
              {lags.map(([source, hours]) => `${humanize(source)}: ${String(hours)} h`).join("; ")}
            </span>
          </Stat>
          <Stat label="Outcome window" hint="Never includes a day that had begun before the recommendation.">
            The first whole UTC days at or after the recommendation
          </Stat>
        </dl>
      </Section>

      <Section
        id="strategies"
        title="Score v0 against simple alternatives"
        note="The same games on the same days, ordered four ways, each judged by the next 72 hours against the baseline known at the snapshot. Rank association is the mean daily Spearman correlation between position and outcome."
      >
        <Strategies strategies={report.strategies} scoreVersion={report.score_version} />
        <p className="mt-2 text-xs text-muted">
          Hit rate: share at or above the hit threshold. Downside rate: share at or below the downside threshold. A
          group with n of 0 shows no statistics. All ranked outcomes together: mean{" "}
          {formatOutcome(report.overall.mean)}, median {formatOutcome(report.overall.median)}, lowest{" "}
          {formatOutcome(report.overall.minimum)}, highest {formatOutcome(report.overall.maximum)} (n ={" "}
          {formatCount(report.overall.count)}).
        </p>
      </Section>

      <Section id="score-bins" title="Outcome by score" note="Fixed score bins; the first is a score of exactly 0.">
        <Strata strata={report.score_bins} caption="Primary outcome by score bin" first="Score v0" />
      </Section>

      <Section id="confidence" title="Outcome by confidence band">
        <Strata strata={report.confidence_bands} caption="Primary outcome by confidence band" first="Confidence" />
      </Section>

      <Section id="secondary" title="Secondary outcomes" note="Over the games that were ranked.">
        <dl className="panel grid grid-cols-2 gap-x-6 gap-y-5 p-5 md:grid-cols-4">
          <Stat label="Next-day movement, mean" hint={`median ${formatOutcome(report.movement_24h.median)}; n = ${formatCount(report.movement_24h.count)}`}>
            <span className="num font-mono">{formatOutcome(report.movement_24h.mean)}</span>
          </Stat>
          <Stat label="Week above baseline, mean" hint={`median ${formatRate(report.persistence_7d.median)}; n = ${formatCount(report.persistence_7d.count)}`}>
            <span className="num font-mono">{formatRate(report.persistence_7d.mean)}</span>
          </Stat>
          <Stat label="Days to next peak, median" hint={`mean ${formatDecimal(report.lead_time.mean_days)}; found ${formatCount(report.lead_time.found)}`}>
            <span className="num font-mono">{formatDecimal(report.lead_time.median_days)}</span>
          </Stat>
          <Stat label="No peak in window" hint={`${formatCount(report.lead_time.unavailable)} unavailable`}>
            <span className="num font-mono">{formatCount(report.lead_time.none_in_window)}</span>
          </Stat>
        </dl>
      </Section>

      <Section id="evidence" title="Why this status" note={report.evidence.holdout_rationale}>
        <div className="panel overflow-x-auto">
          <table data-testid="evidence-table" className="w-full border-collapse text-sm">
            <caption className="sr-only">Requirements for each evidence status</caption>
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th scope="col" className="px-4 py-2 text-left font-medium">Status</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Reached</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Requirements (actual / required)</th>
              </tr>
            </thead>
            <tbody>
              {report.evidence.checks.map((check) => (
                <tr key={check.status} className="border-b border-line align-top last:border-b-0">
                  <th scope="row" className="px-4 py-3 text-left font-normal">
                    <span className="text-ink">{humanize(check.status)}</span>
                    <span className="mt-1 block max-w-sm text-xs text-muted">{check.rationale}</span>
                  </th>
                  <td className="px-3 py-3">
                    <Badge tone={check.reached ? "good" : "neutral"} glyph>
                      {check.reached ? "Yes" : "No"}
                    </Badge>
                  </td>
                  <td className="px-3 py-3">
                    <ul className="space-y-1">
                      {check.requirements.map((requirement) => (
                        <li key={requirement.name} className="num flex gap-3 font-mono text-xs">
                          <span className={requirement.met ? "text-up" : "text-down"}>
                            <span aria-hidden="true">{requirement.met ? "✓" : "✗"}</span>
                            <span className="sr-only">{requirement.met ? "met" : "not met"}</span>
                          </span>
                          <span className="w-44 text-ink-soft">{requirement.name}</span>
                          <span className="text-ink">
                            {formatCount(requirement.actual)} / {formatCount(requirement.required)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section id="leakage" title="Look-ahead checks" note="Run during the backtest, not assumed.">
        <ul className="panel divide-y divide-line">
          {report.leakage_checks.map((check) => (
            <li key={check.name} className="flex flex-wrap items-start gap-x-4 gap-y-1 px-4 py-3 text-sm">
              <Badge tone={check.passed ? "good" : "bad"} glyph>
                {check.passed ? "Passed" : "Failed"}
              </Badge>
              <span className="min-w-0 flex-1">
                <span className="font-mono text-xs text-ink">{check.name}</span>
                <span className="mt-0.5 block text-ink-soft">{check.description}</span>
              </span>
              <span className="num text-xs text-muted">{formatCount(check.checked)} checked</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="events" title="Events">
        <aside aria-label="Event backtesting" className="panel border-l-2 border-l-info px-5 py-4">
          <p className="eyebrow text-info">
            {report.events.real_event_history ? "Event history present" : "Event backtesting unavailable"}
          </p>
          <p data-testid="event-sources-used" className="mt-1.5 max-w-4xl text-sm text-ink">
            {report.events.event_sources_used.length > 0 ? (
              <>
                Event sources used:{" "}
                <span className="font-mono text-xs">{report.events.event_sources_used.join(", ")}</span>
              </>
            ) : (
              "No event source took part: no historical prediction saw an event or an event-source check."
            )}
          </p>
          <p className="mt-1.5 max-w-4xl text-sm text-ink-soft">{report.events.note}</p>
          <p className="num mt-1.5 text-xs text-muted">
            Snapshots that carried event intelligence: {formatCount(report.events.snapshots_with_event_intelligence)} of{" "}
            {formatCount(report.recommendations)}; with a heat above 0:{" "}
            {formatCount(report.events.snapshots_with_positive_event_heat)}. Rules: <span className="font-mono">{report.event_rules_version}</span>.
          </p>
        </aside>
      </Section>

      <OutcomeDefinition validation={validation} />

      <Section
        id="inspection"
        title="A past day, and what followed"
        note={
          report.inspection_truncated
            ? `The most recent ${report.inspection.length} recommendation days are kept in the report. Every snapshot is in the accountability store.`
            : undefined
        }
      >
        <Inspection days={report.inspection} />
      </Section>

      <Section id="limitations" title="Limitations" note="Stated by the report itself.">
        <ol className="panel list-decimal space-y-2.5 py-5 pr-5 pl-10 text-sm text-ink-soft marker:font-mono marker:text-xs marker:text-muted">
          {report.limitations.map((limitation) => (
            <li key={limitation} className="max-w-3xl pl-1">
              {limitation}
            </li>
          ))}
        </ol>
      </Section>

      <Section
        id="provenance"
        title="Provenance"
        note="Which records this report summarises. Each hash is a SHA-256 over the sorted record ids, so the same records always give the same hash and one changed record gives another."
      >
        <dl data-testid="provenance" className="panel grid gap-y-4 p-5">
          <Stat label={`Snapshots (${formatCount(report.recommendations)})`}>
            <span className="font-mono text-xs break-all">{report.snapshot_set_hash}</span>
          </Stat>
          <Stat label="Outcome labels">
            <span className="font-mono text-xs break-all">{report.label_set_hash}</span>
          </Stat>
          <Stat label="Report">
            <span className="font-mono text-xs break-all">
              {report.report_id} · {report.format} · backtest run {formatTimestamp(report.created_at)}
            </span>
          </Stat>
        </dl>
      </Section>
    </>
  );
}

export function ValidationBody({ validation }: { validation: ValidationArtifact }) {
  const report = validation.report ?? null;
  if (validation.availability !== "available" || report === null) {
    return (
      <>
        <div data-testid="validation-not-run" className="panel border-l-2 border-l-info p-6">
          <p className="eyebrow text-info">No backtest in this bundle</p>
          <h2 className="mt-2 text-lg font-semibold text-ink">Historical validation has not been run</h2>
          <p className="mt-2 max-w-3xl text-sm text-ink-soft">
            This is not a result. Nothing has been measured, so there is no number to show: not zero, not good, not
            bad.
          </p>
          <p className="mt-3 max-w-3xl text-sm text-muted">{validation.scope_note}</p>
          <p className="mt-3 max-w-3xl text-sm text-muted">
            Run it offline with <code className="font-mono text-ink-soft">npm run data:replay</code>, which backtests
            the recorded history before syncing.
          </p>
        </div>
        <OutcomeDefinition validation={validation} />
      </>
    );
  }
  return <Report validation={validation} report={report} />;
}

export function Validation() {
  const state = useArtifact("validation", loadValidation);
  return (
    <DataState state={state} label="historical validation" rows={12}>
      {({ manifest, data }) => (
        <>
          <ValidationBody validation={data} />
          <RunNote manifest={manifest} />
        </>
      )}
    </DataState>
  );
}
