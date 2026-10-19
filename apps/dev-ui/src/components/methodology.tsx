"use client";

import type { EventHeatMethodology, MethodologyArtifact, SourceMethodology, TermView } from "@/contract";
import { loadMethodology } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";
import { formatConfidence, formatCount, formatDay, formatDecimal, humanize } from "@/lib/format";

import { DataState } from "./data-state";
import { RunNote } from "./run-note";
import { Badge, Section, Stat } from "./ui";

function Terms({ terms, caption }: { terms: readonly TermView[]; caption: string }) {
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th scope="col" className="px-4 py-2 text-left font-medium">Term</th>
            <th scope="col" className="px-4 py-2 text-left font-medium">Formula</th>
            <th scope="col" className="px-4 py-2 text-left font-medium">Why</th>
          </tr>
        </thead>
        <tbody>
          {terms.map((term) => (
            <tr key={term.name} className="border-b border-line align-top last:border-b-0">
              <th scope="row" className="px-4 py-3 text-left font-mono text-sm font-medium whitespace-nowrap text-ink">
                {term.name}
              </th>
              <td className="px-4 py-3 font-mono text-xs text-signal">{term.formula}</td>
              <td className="max-w-xl px-4 py-3 text-ink-soft">{term.rationale}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PERMISSION_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  allowed: "good",
  not_required: "neutral",
  required: "warn",
  restricted: "warn",
  unknown: "warn",
  prohibited: "bad",
  not_allowed: "bad",
};

function Permission({ value }: { value: string }) {
  return (
    <Badge tone={PERMISSION_TONE[value] ?? "neutral"} glyph>
      {humanize(value)}
    </Badge>
  );
}

function SourceBlock({ source }: { source: SourceMethodology }) {
  const policy = source.policy;
  const permissions: [string, string][] = [
    ["Raw storage", policy.raw_storage_allowed],
    ["Historical storage", policy.historical_storage_allowed],
    ["Derived metrics", policy.derived_metrics_allowed],
    ["Commercial use", policy.commercial_use_allowed],
    ["Redistribution", policy.redistribution_allowed],
    ["Attribution", policy.attribution_required],
    ["Approval", policy.approval_status],
    ["Link back", policy.display.link_back_required],
    ["Branding", policy.display.branding_required],
  ];
  return (
    <article aria-labelledby={`method-source-${source.source}`} className="panel p-5">
      <h3 id={`method-source-${source.source}`} className="text-base font-semibold text-ink">
        {humanize(source.source)}
        <span className="ml-3 font-mono text-xs font-normal text-muted">{source.source}</span>
      </h3>
      <p className="mt-1.5 max-w-3xl text-sm text-ink-soft">{source.description}</p>

      <dl className="mt-5 grid gap-x-8 gap-y-5 md:grid-cols-3">
        <Stat label="Signals">
          <ul>
            {source.signals.map((signal) => (
              <li key={`${signal.metric}-${signal.granularity}`}>
                {humanize(signal.metric)}, {signal.granularity}
              </li>
            ))}
          </ul>
        </Stat>
        <Stat label="Attribution text">{policy.attribution_text ?? "None required"}</Stat>
        <Stat label="Policy reviewed">
          <span className="num">{formatDay(policy.reviewed_at)}</span>
        </Stat>
      </dl>

      <h4 className="eyebrow mt-6">Usage policy</h4>
      <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3">
        {permissions.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3 border-b border-line pb-2">
            <dt className="text-sm text-ink-soft">{label}</dt>
            <dd>
              <Permission value={value} />
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 max-w-3xl text-xs text-muted">{policy.display.note}</p>

      <h4 className="eyebrow mt-6">Stage clearance</h4>
      <ul className="mt-2 divide-y divide-line">
        {policy.stage_clearance.map((stage) => (
          <li key={stage.stage} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2 text-sm">
            <span className="w-48 text-ink">{humanize(stage.stage)}</span>
            <Badge tone={stage.cleared ? "good" : "bad"} glyph>
              {stage.cleared ? "Cleared" : "Not cleared"}
            </Badge>
            <span className="text-ink-soft">{stage.reason}</span>
          </li>
        ))}
      </ul>

      {policy.notes ? <p className="mt-4 max-w-3xl text-xs text-muted">{policy.notes}</p> : null}
    </article>
  );
}

function days(value: number): string {
  return value === 0 ? "none" : `${formatCount(value)} d`;
}

function EventHeatSection({ rules }: { rules: EventHeatMethodology }) {
  return (
    <Section
      id="event-heat"
      title={`Event Heat: ${rules.version}`}
      note={`A value from ${formatCount(rules.range[0])} to ${formatCount(rules.range[1])}. ${humanize(rules.status)}: every number below is an engineering prior.`}
    >
      <aside aria-label="Status of Event Heat" className="panel mb-4 border-l-2 border-l-signal px-5 py-4">
        <p className="eyebrow text-signal">Internal, uncalibrated, and not in score v0</p>
        <p className="mt-1.5 max-w-4xl text-sm text-ink-soft">{rules.scope_note}</p>
      </aside>
      <Terms terms={rules.terms} caption="Event Heat terms" />
      <h3 className="eyebrow mt-6 mb-2">Availability</h3>
      <Terms terms={rules.availability} caption="Event Heat availability" />
      <h3 className="eyebrow mt-6 mb-2">Parameters</h3>
      <div className="panel overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Event Heat parameters</caption>
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th scope="col" className="px-4 py-2 text-left font-medium">Parameter</th>
              <th scope="col" className="px-4 py-2 text-left font-medium">Value</th>
              <th scope="col" className="px-4 py-2 text-left font-medium">Why</th>
            </tr>
          </thead>
          <tbody>
            {rules.parameters.map((parameter) => (
              <tr key={parameter.name} className="border-b border-line align-top last:border-b-0">
                <th scope="row" className="px-4 py-3 text-left font-mono text-xs font-medium text-ink">
                  {parameter.name}
                </th>
                <td className="num px-4 py-3 font-mono text-xs whitespace-nowrap text-signal">{parameter.value}</td>
                <td className="max-w-xl px-4 py-3 text-ink-soft">{parameter.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="eyebrow mt-6 mb-2">Rules by event type</h3>
      <div className="panel overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Event Heat rules by event type</caption>
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th scope="col" className="px-4 py-2 text-left font-medium">Type</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Importance</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Ramp</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Pre-event peak</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Default length</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Half-life</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Floor</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Decay window</th>
              <th scope="col" className="px-4 py-2 text-left font-medium">Why</th>
            </tr>
          </thead>
          <tbody>
            {rules.types.map((rule) => (
              <tr key={rule.event_type} className="border-b border-line align-top last:border-b-0">
                <th scope="row" className="px-4 py-3 text-left font-normal whitespace-nowrap">
                  <span className="text-ink">{rule.label}</span>
                  <span className="block font-mono text-xs text-muted">{rule.event_type}</span>
                </th>
                <td className="num px-3 py-3 text-right font-mono">{formatDecimal(rule.importance)}</td>
                <td className="num px-3 py-3 text-right font-mono">{days(rule.ramp_days)}</td>
                <td className="num px-3 py-3 text-right font-mono">{formatDecimal(rule.pre_event_peak)}</td>
                <td className="num px-3 py-3 text-right font-mono">{days(rule.default_duration_days)}</td>
                <td className="num px-3 py-3 text-right font-mono">{days(rule.half_life_days)}</td>
                <td className="num px-3 py-3 text-right font-mono">{formatDecimal(rule.active_floor)}</td>
                <td className="num px-3 py-3 text-right font-mono">{days(rule.decay_window_days)}</td>
                <td className="min-w-64 px-4 py-3 text-xs text-ink-soft">{rule.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="eyebrow mt-6 mb-2">Event Heat limitations</h3>
      <ul className="panel list-disc space-y-2 py-4 pr-5 pl-9 text-sm text-ink-soft marker:text-muted">
        {rules.limitations.map((limitation) => (
          <li key={limitation} className="max-w-3xl">
            {limitation}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Body({ methodology }: { methodology: MethodologyArtifact }) {
  return (
    <>
      <aside aria-label="What score v0 is" className="panel border-l-2 border-l-signal px-5 py-4">
        <p className="text-base font-semibold tracking-tight text-ink">
          Internal score v0 <span aria-hidden="true">≠</span>
          <span className="sr-only">is not</span> the final MetaSpike creator-opportunity model
        </p>
        <p className="mt-2 max-w-4xl text-sm text-ink-soft">{methodology.scope_note}</p>
        <p className="mt-2 max-w-4xl text-sm text-muted">
          Everything on this page is rendered from the methodology artifact of the current run. It describes{" "}
          <code className="font-mono text-ink-soft">{methodology.score.version}</code>, an internal engineering score.
          The final opportunity model has not been defined and is not described here.
        </p>
      </aside>

      <Section
        id="score"
        title={`Score: ${methodology.score.version}`}
        note={`A value from ${formatCount(methodology.score.range[0])} to ${formatCount(methodology.score.range[1])}, built from these terms.`}
      >
        <Terms terms={methodology.score.terms} caption="Score terms" />
      </Section>

      <Section
        id="confidence"
        title="Confidence"
        note={`A value from ${formatCount(methodology.confidence.range[0])} to ${formatCount(methodology.confidence.range[1])}. It says how far the data supports the score, not how good the opportunity is.`}
      >
        <dl className="panel mb-4 grid gap-x-8 gap-y-5 p-5 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
          <Stat label="Current ceiling" hint="The highest confidence any game can have today">
            <span className="num font-mono text-2xl font-semibold">{formatConfidence(methodology.confidence.current_ceiling)}</span>
            <span className="ml-1 text-muted">/ 100</span>
          </Stat>
          <Stat label="Bands">
            <ul className="space-y-1">
              {Object.entries(methodology.confidence.bands).map(([band, range]) => (
                <li key={band} className="flex gap-3">
                  <span className="w-20 text-ink">{humanize(band)}</span>
                  <span className="num text-ink-soft">{range}</span>
                </li>
              ))}
            </ul>
          </Stat>
        </dl>
        <Terms terms={methodology.confidence.terms} caption="Confidence terms" />
      </Section>

      <EventHeatSection rules={methodology.event_heat} />

      <Section
        id="features"
        title="Features"
        note={`${humanize(methodology.features.granularity)} values over a ${formatCount(methodology.features.history_days)}-day window.`}
      >
        <p className="panel mb-4 px-5 py-4 text-sm text-ink-soft">{methodology.features.principle}</p>
        <div className="panel overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Feature definitions</caption>
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th scope="col" className="px-4 py-2 text-left font-medium">Feature</th>
                <th scope="col" className="px-4 py-2 text-left font-medium">Unit</th>
                <th scope="col" className="px-4 py-2 text-left font-medium">Definition</th>
                <th scope="col" className="px-4 py-2 text-left font-medium">Valid when</th>
              </tr>
            </thead>
            <tbody>
              {methodology.features.definitions.map((definition) => (
                <tr key={definition.name} className="border-b border-line align-top last:border-b-0">
                  <th scope="row" className="px-4 py-3 text-left font-mono text-xs font-medium text-ink">
                    {definition.name}
                  </th>
                  <td className="px-4 py-3 text-xs whitespace-nowrap text-muted">{definition.unit}</td>
                  <td className="max-w-xl px-4 py-3 text-ink-soft">{definition.definition}</td>
                  <td className="px-4 py-3 text-xs text-muted">{definition.valid_when}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section id="explanations" title="Explanations">
        <p className="panel px-5 py-4 text-sm text-ink-soft">{methodology.explanations}</p>
      </Section>

      <Section id="metrics" title="Metrics">
        <ul className="panel divide-y divide-line">
          {methodology.metrics.map((metric) => (
            <li key={metric.metric_id} className="px-5 py-4">
              <p className="text-sm font-medium text-ink">
                {metric.display_name}
                <span className="ml-3 font-mono text-xs font-normal text-muted">{metric.metric_id}</span>
              </p>
              <p className="mt-1 max-w-3xl text-sm text-ink-soft">{metric.description}</p>
              <p className="mt-1.5 text-xs text-muted">
                Unit {metric.unit} · role {metric.role} · aggregated by {metric.aggregation} ·{" "}
                {metric.granularities.join(", ")}
              </p>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="sources" title="Sources" note="What is collected, and the usage policy recorded for it.">
        <div className="space-y-4">
          {methodology.sources.map((source) => (
            <SourceBlock key={source.source} source={source} />
          ))}
        </div>
      </Section>

      <Section id="limitations" title="Limitations" note="Stated by the run itself.">
        <ol className="panel list-decimal space-y-2.5 py-5 pr-5 pl-10 text-sm text-ink-soft marker:font-mono marker:text-xs marker:text-muted">
          {methodology.limitations.map((limitation) => (
            <li key={limitation} className="max-w-3xl pl-1">
              {limitation}
            </li>
          ))}
        </ol>
      </Section>
    </>
  );
}

export function Methodology() {
  const state = useArtifact("methodology", loadMethodology);
  return (
    <DataState state={state} label="the methodology" rows={12}>
      {({ manifest, data }) => (
        <>
          <Body methodology={data} />
          <RunNote manifest={manifest} />
        </>
      )}
    </DataState>
  );
}
