// The private creator-opportunity views. Pure rendering of a validated report: no value
// is computed, ranked, rescored or reclassified here. The order is the engine's.
import Link from "next/link";

import { type Brief, type BriefItem, buildBrief, briefText, READINESS_WORDS, UNVALIDATED } from "@/private/opportunities/brief";
import {
  type Comparison,
  comparisonText,
  MAX_GAMES,
  MIN_GAMES,
  NO_GENUINE_OPPORTUNITY,
  ORDER_NOTE,
} from "@/private/opportunities/compare";
import type { Loaded, View } from "@/private/opportunities/loader";
import type {
  MovementNote,
  OpportunityAssessment,
  OpportunityReport,
  Readiness,
  ShortlistEntry,
  Standing,
} from "@/private/opportunities/types";

import { CopyText } from "./copy-text";
import { Badge, Section } from "./ui";

export const NO_REAL_OPPORTUNITY =
  "No real creator opportunity is established by the currently verified evidence.";
export const SYNTHETIC_HEADLINE = "SYNTHETIC DEMONSTRATION — NOT A REAL RECOMMENDATION";
const TIE = "indistinguishable on the evidence";

const READINESS: Record<Readiness, { word: string; tone: "good" | "warn" | "neutral" }> = {
  evidence_supported_candidate: { word: "Evidence-supported candidate", tone: "good" },
  provisional_watchlist: { word: "Provisional · watchlist", tone: "warn" },
  insufficient_evidence: { word: "Insufficient evidence", tone: "neutral" },
};

const STANDING: Record<Standing, { word: string; tone: "good" | "bad" | "warn" | "neutral" }> = {
  verified: { word: "verified", tone: "good" },
  synthetic: { word: "SYNTHETIC", tone: "bad" },
  unverified: { word: "UNVERIFIED", tone: "warn" },
  none: { word: "no input", tone: "neutral" },
};

function moment(value: string | null): string {
  if (value === null) return "—";
  return `${value.replace("T", " ").replace(/:\d\d(\.\d+)?(Z|\+00:00)$/, "")} UTC`;
}

export function ReadinessBadge({ readiness, simulated = false }: { readiness: Readiness; simulated?: boolean }) {
  const shown = READINESS[readiness];
  return (
    <Badge tone={simulated ? "bad" : shown.tone} glyph={!simulated}>
      {simulated ? `SIMULATED: ${shown.word}` : shown.word}
    </Badge>
  );
}

export function StandingBadge({ standing }: { standing: Standing }) {
  return <Badge tone={STANDING[standing].tone}>{STANDING[standing].word}</Badge>;
}

function href(slug: string, view: View): string {
  return view === "synthetic" ? `/opportunities/${slug}?view=synthetic` : `/opportunities/${slug}`;
}

function movementText(m: MovementNote): string {
  const values =
    m.log_lift === null || m.robust_z === null
      ? "no measurement"
      : `log lift ${m.log_lift.toPrecision(3)}, robust z ${m.robust_z.toPrecision(3)}`;
  return `${m.family.replaceAll("_", " ")} ${m.direction} (${values})`;
}

function supplyState(a: OpportunityAssessment): string {
  // Words for the engine's own creator-coverage statement. Nothing is inferred.
  const text = a.creator_coverage;
  if (text.startsWith("No creator-supply input")) return "not collected";
  if (text.includes("Incomplete sample")) return "incomplete sample";
  if (text.includes("returned no video")) return "complete sample, no videos";
  if (text.includes("Ambiguous")) return "ambiguous sample";
  if (text.includes("Not queried")) return "not queried";
  if (text.includes("Expired")) return "expired";
  return "sample (a lower bound)";
}

export function Banners({ report, view }: { report: OpportunityReport; view: View }) {
  const noneReal = report.entries.every((entry) => entry.readiness === "insufficient_evidence");
  return (
    <div className="space-y-3">
      {view === "synthetic" ? (
        <div role="alert" className="panel border-bad/60 p-4" data-testid="synthetic-banner">
          <p className="text-base font-semibold tracking-wide text-bad">{SYNTHETIC_HEADLINE}</p>
          <p className="mt-1 text-sm text-ink-soft">
            Every demand series, sample, announcement, video and post behind this view was invented to exercise the
            rubric. Simulated readiness counts invented evidence as if it were real. It describes no game&apos;s
            actual state.
          </p>
        </div>
      ) : null}
      {view === "verified" && report.contains_synthetic ? (
        <div role="alert" className="panel border-warn/60 p-4">
          <p className="text-sm text-warn">
            This report contains synthetic inputs. Only verified readiness is shown here; synthetic evidence counts
            for nothing in it.
          </p>
        </div>
      ) : null}
      {noneReal ? (
        <p className="panel p-4 text-base font-medium text-ink" data-testid="no-real-opportunity">
          {NO_REAL_OPPORTUNITY}
        </p>
      ) : null}
      <aside aria-label="How to read this" className="panel p-4 text-sm text-ink-soft">
        <p>
          Assessed as of <span className="font-mono text-ink">{moment(report.as_of)}</span> with{" "}
          <span className="font-mono">{report.rules_version}</span>. Private development only; never published.
        </p>
        <p className="mt-1">
          The rubric&apos;s thresholds are <strong className="text-warn">unvalidated</strong>: no historical
          evaluation has shown they mark real opportunities. Nothing here is a probability or a calibrated
          confidence. Missing creator coverage is never low competition, and missing discussion is never low
          interest.
        </p>
        <p className="mt-1">
          Method: <code className="font-mono">docs/creator-opportunity-intelligence.md</code>
        </p>
      </aside>
    </div>
  );
}

function Shortlist({
  report,
  view,
  entries,
  simulated,
}: {
  report: OpportunityReport;
  view: View;
  entries: ShortlistEntry[];
  simulated: boolean;
}) {
  const byId = new Map(report.assessments.map((a) => [a.canonical_game_id, a]));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm" data-testid={simulated ? "simulated-shortlist" : "verified-shortlist"}>
        <thead className="text-xs text-muted">
          <tr>
            <th className="py-2 pr-3">#</th>
            <th className="py-2 pr-3">Game</th>
            <th className="py-2 pr-3">{simulated ? "Simulated readiness" : "Verified readiness"}</th>
            <th className="py-2 pr-3">Why now</th>
            <th className="py-2 pr-3">Angle</th>
            <th className="py-2 pr-3">Creator coverage</th>
            <th className="py-2 pr-3">Above the next because</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, index) => {
            const a = byId.get(entry.canonical_game_id);
            if (!a) return null;
            const tiedAbove = index > 0 && (entries[index - 1]?.ahead_of_next ?? "").startsWith(TIE);
            const statements = simulated ? [...a.why_now, ...a.unconfirmed] : a.why_now;
            return (
              <tr key={entry.canonical_game_id} className="border-t border-line align-top" data-testid="shortlist-row">
                <td className="py-2 pr-3 font-mono text-muted">
                  {tiedAbove ? <span title="tied with the game above">=</span> : entry.position}
                </td>
                <td className="py-2 pr-3">
                  <Link href={href(entry.slug, view)} className="text-ink underline decoration-line-strong">
                    {a.name}
                  </Link>
                </td>
                <td className="py-2 pr-3">
                  <ReadinessBadge readiness={entry.readiness} simulated={simulated} />
                </td>
                <td className="py-2 pr-3 text-ink-soft">
                  {statements.length > 0
                    ? statements.map((s, i) => (
                        <span key={i} className="block">
                          {simulated && s.standing !== "verified" ? `[${STANDING[s.standing].word}] ` : ""}
                          {s.text}
                        </span>
                      ))
                    : "Nothing verified says “now”."}
                </td>
                <td className="py-2 pr-3 text-ink-soft">
                  {(simulated ? a.simulated_facts.angle : a.facts.angle) ? "established" : "not yet established"}
                </td>
                <td className="py-2 pr-3 text-ink-soft">{supplyState(a)}</td>
                <td className="py-2 pr-3 text-xs text-muted">
                  {entry.ahead_of_next.startsWith(TIE)
                    ? "Indistinguishable on the evidence — ordered by id only, which means nothing"
                    : entry.ahead_of_next}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function OpportunityBoard({ report, view }: { report: OpportunityReport; view: View }) {
  return (
    <>
      <Banners report={report} view={view} />
      <nav aria-label="Report" className="mt-6 flex gap-4 text-sm">
        <Link href="/opportunities" aria-current={view === "verified" ? "page" : undefined} className="underline">
          Verified evidence only
        </Link>
        <Link
          href="/opportunities?view=synthetic"
          aria-current={view === "synthetic" ? "page" : undefined}
          className="underline"
        >
          Synthetic demonstration
        </Link>
      </nav>
      <Section
        id="verified"
        title="Shortlist — verified evidence only"
        note={`${report.summary} The order is the engine's own; ties are said to be ties.`}
      >
        <Shortlist report={report} view={view} entries={report.entries} simulated={false} />
      </Section>
      <Section
        id="compare"
        title="Compare games"
        note={`Choose ${MIN_GAMES} or ${MAX_GAMES} games to see their evidence side by side. The comparison keeps the engine's order and ranks nothing.`}
      >
        <CompareSelector report={report} view={view} />
      </Section>
      {view === "synthetic" ? (
        <Section
          id="simulated"
          title={`Simulated order — ${SYNTHETIC_HEADLINE}`}
          note="The same rubric, counting synthetic and unverified evidence as if it were real. For inspecting the rule only."
        >
          <Shortlist report={report} view={view} entries={report.simulated_entries} simulated />
        </Section>
      ) : null}
    </>
  );
}

function BriefList({ items, empty }: { items: BriefItem[]; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-ink-soft">{empty}</p>;
  return (
    <ul className="space-y-1 text-sm text-ink-soft">
      {items.map((i, n) => (
        <li key={n}>
          {i.standing !== null && i.standing !== "verified" ? <StandingBadge standing={i.standing} /> : null}{" "}
          {i.text}
          {i.references.length ? <span className="text-xs text-muted"> (source: {i.references.join(", ")})</span> : null}
        </li>
      ))}
    </ul>
  );
}

function BriefPart({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <p className="eyebrow">{title}</p>
      <div className="mt-1">{children}</div>
    </div>
  );
}

/** One game's decision brief: the engine's assessment in plain words, and as copyable text. */
export function CreatorBrief({ brief }: { brief: Brief }) {
  const text = briefText(brief);
  return (
    <div
      className={`panel p-5 ${brief.synthetic ? "border-bad/60" : ""}`}
      data-testid="creator-brief"
      data-basis={brief.basis}
    >
      {brief.synthetic ? (
        <p role="alert" className="mb-3 text-base font-semibold tracking-wide text-bad" data-testid="brief-synthetic">
          {SYNTHETIC_HEADLINE}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <ReadinessBadge readiness={brief.readiness} simulated={brief.synthetic} />
        <span className="text-xs text-muted">
          {brief.synthetic
            ? `simulated; verified readiness: ${READINESS_WORDS[brief.verifiedReadiness]}`
            : "verified evidence only"}
          {" · "}as of {moment(brief.asOf)}
          {brief.position !== null ? ` · position ${brief.position} in the engine's order` : ""}
        </span>
      </div>
      <p className="mt-3 text-base font-medium text-ink" data-testid="brief-decision">
        {brief.decision}
      </p>
      {brief.decisionDetail.map((d, i) => (
        <p key={i} className="mt-1 text-sm text-ink-soft">
          {d}
        </p>
      ))}
      {brief.notices
        .filter((n) => n !== SYNTHETIC_HEADLINE)
        .map((n, i) => (
          <p key={i} className="mt-2 text-sm text-warn">
            {n}
          </p>
        ))}

      <BriefPart title="Rubric conditions (the engine's facts)">
        <ul className="space-y-1 text-sm text-ink-soft" data-testid="brief-conditions">
          {brief.conditions.map((c) => (
            <li key={c.label}>
              <Badge tone={c.met ? "good" : "neutral"}>{c.met ? "met" : "not met"}</Badge> {c.label}
            </li>
          ))}
        </ul>
      </BriefPart>
      <BriefPart title="What changed">
        <BriefList items={brief.changed} empty="No demand family has a current measurement for this game." />
      </BriefPart>
      <BriefPart title={brief.synthetic ? "Why now (simulated)" : "Why now (verified)"}>
        <BriefList items={brief.whyNow} empty="Nothing counted says “now”." />
      </BriefPart>
      {brief.notCounted.length ? (
        <BriefPart title="Pointing somewhere, but not counted">
          <BriefList items={brief.notCounted} empty="" />
        </BriefPart>
      ) : null}
      <BriefPart title="Video angle">
        <p className="text-sm text-ink-soft" data-testid="brief-angle-note">
          {brief.angleNote}
        </p>
        {brief.angles.length ? <BriefList items={brief.angles} empty="" /> : null}
      </BriefPart>
      <BriefPart title="Creator supply">
        <ul className="space-y-1 text-sm text-ink-soft" data-testid="brief-supply">
          {brief.creatorSupply.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      </BriefPart>
      <BriefPart title="Community">
        <ul className="space-y-1 text-sm text-ink-soft">
          {brief.community.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      </BriefPart>
      <BriefPart title="Counterevidence and caveats">
        <BriefList items={brief.counterevidence} empty="None recorded." />
      </BriefPart>
      <BriefPart title="Confirm before acting">
        <ul className="list-disc pl-5 text-sm text-ink-soft" data-testid="brief-confirm">
          {brief.confirmFirst.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted">{UNVALIDATED}</p>
      </BriefPart>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <CopyText text={text} label="Copy brief as text" />
      </div>
      <details className="mt-3">
        <summary className="cursor-pointer text-sm text-ink-soft">Plain-text brief</summary>
        <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-ink-soft" data-testid="brief-text">
          {text}
        </pre>
      </details>
    </div>
  );
}

/** A plain form: the server validates whatever it is sent. No script is needed. */
export function CompareSelector({ report, view }: { report: OpportunityReport; view: View }) {
  const byId = new Map(report.assessments.map((a) => [a.canonical_game_id, a]));
  return (
    <form method="get" action="/opportunities/compare" data-testid="compare-selector">
      <fieldset>
        <legend className="text-sm text-ink-soft">Games, in the engine&apos;s verified order</legend>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
          {report.entries.map((entry) => {
            const a = byId.get(entry.canonical_game_id);
            if (!a) return null;
            return (
              <label key={entry.slug} className="inline-flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" name="game" value={entry.slug} /> {a.name}
              </label>
            );
          })}
        </div>
      </fieldset>
      {view === "synthetic" ? <input type="hidden" name="view" value="synthetic" /> : null}
      <button
        type="submit"
        className="mt-3 rounded border border-line-strong px-2 py-1 text-xs font-medium text-ink hover:border-signal"
      >
        Compare selected
      </button>
    </form>
  );
}

function Cell({ items, empty }: { items: BriefItem[]; empty: string }) {
  return <BriefList items={items} empty={empty} />;
}

/** Two or three games side by side, in the engine's order. Words are each game's brief. */
export function OpportunityComparison({ comparison, view }: { comparison: Comparison; view: View }) {
  const c = comparison;
  const text = comparisonText(c);
  const rows: { title: string; render: (b: Brief) => React.ReactNode }[] = [
    {
      title: "Readiness",
      render: (b) => (
        <div className="space-y-1">
          <ReadinessBadge readiness={b.readiness} simulated={c.synthetic} />
          {c.synthetic ? (
            <p className="text-xs text-muted">verified readiness: {READINESS_WORDS[b.verifiedReadiness]}</p>
          ) : null}
          <p className="text-sm text-ink-soft">{b.decision}</p>
        </div>
      ),
    },
    { title: "Why now (counted evidence)", render: (b) => <Cell items={b.whyNow} empty="Nothing counted says “now”." /> },
    { title: "Leads, not counted", render: (b) => <Cell items={b.notCounted} empty="None." /> },
    {
      title: "What changed",
      render: (b) => <Cell items={b.changed} empty="No demand family has a current measurement." />,
    },
    {
      title: "Video angle",
      render: (b) => (
        <div>
          <p className="text-sm text-ink-soft">{b.angleNote}</p>
          {b.angles.length ? <Cell items={b.angles} empty="" /> : null}
        </div>
      ),
    },
    {
      title: "Creator coverage",
      render: (b) => (
        <ul className="space-y-1 text-sm text-ink-soft">
          {b.creatorSupply.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      ),
    },
    {
      title: "Community",
      render: (b) => (
        <ul className="space-y-1 text-sm text-ink-soft">
          {b.community.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      ),
    },
    {
      title: "Events",
      render: (b) => (
        <ul className="space-y-1 text-sm text-ink-soft">
          {b.events.length === 0 ? <li>No upcoming or active event is known.</li> : null}
          {b.events.map((e, i) => (
            <li key={i}>
              {e.standing !== null && e.standing !== "verified" ? <StandingBadge standing={e.standing} /> : null}{" "}
              {e.text} {e.counted ? null : <span className="text-xs text-warn">lead, not counted</span>}
            </li>
          ))}
        </ul>
      ),
    },
    { title: "Counterevidence", render: (b) => <Cell items={b.counterevidence} empty="None recorded." /> },
    {
      title: "Not known",
      render: (b) => (
        <ul className="list-disc pl-5 text-sm text-ink-soft">
          {b.missing.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      ),
    },
    {
      title: "Freshness",
      render: (b) => (
        <ul className="space-y-1 text-xs text-ink-soft">
          {b.freshness.map((f) => (
            <li key={f.source}>
              <span className="font-mono">{f.source}</span>: {f.state}; latest {f.latest}{" "}
              <StandingBadge standing={f.standing} />
            </li>
          ))}
        </ul>
      ),
    },
    {
      title: "Confirm before acting",
      render: (b) => (
        <ul className="list-disc pl-5 text-sm text-ink-soft">
          {b.confirmFirst.map((q, i) => (
            <li key={i}>{q}</li>
          ))}
        </ul>
      ),
    },
  ];
  return (
    <div data-testid="comparison" data-basis={c.basis}>
      {c.synthetic ? (
        <p role="alert" className="panel border-bad/60 p-4 text-base font-semibold tracking-wide text-bad" data-testid="comparison-synthetic">
          {SYNTHETIC_HEADLINE}
        </p>
      ) : null}
      {c.noGenuineOpportunity ? (
        <p className="panel mt-3 p-4 text-base font-medium text-ink" data-testid="comparison-abstains">
          {NO_GENUINE_OPPORTUNITY}
        </p>
      ) : null}
      <p className="mt-3 text-sm text-ink-soft">
        Assessed as of <span className="font-mono text-ink">{moment(c.asOf)}</span> with{" "}
        <span className="font-mono">{c.rulesVersion}</span>.{" "}
        {c.synthetic ? "Basis: SIMULATED from synthetic and unverified evidence." : "Basis: verified evidence only."}{" "}
        {ORDER_NOTE}
      </p>
      {c.ties.map((names) => (
        <p key={names.join()} className="mt-1 text-sm text-warn" data-testid="comparison-tie">
          Tied in the engine&apos;s order: {names.join(", ")}. They are ordered by id only, which means nothing.
        </p>
      ))}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full table-fixed text-left align-top text-sm" data-testid="comparison-table">
          <thead>
            <tr>
              <th className="w-40 py-2 pr-3 text-xs text-muted">Engine position</th>
              {c.columns.map((col) => (
                <th key={col.entry.slug} className="py-2 pr-3 align-top" data-testid="comparison-column">
                  <span className="font-mono text-muted">{col.entry.position}.</span>{" "}
                  <Link href={href(col.entry.slug, view)} className="text-ink underline decoration-line-strong">
                    {col.brief.game}
                  </Link>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.title} className="border-t border-line align-top">
                <th scope="row" className="py-2 pr-3 text-xs font-medium text-muted">
                  {row.title}
                </th>
                {c.columns.map((col) => (
                  <td key={col.entry.slug} className="py-2 pr-3">
                    {row.render(col.brief)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4">
        <p className="eyebrow">Questions to answer before choosing</p>
        <ul className="mt-1 list-disc pl-5 text-sm text-ink-soft" data-testid="comparison-questions">
          {c.questions.map((q, i) => (
            <li key={i}>{q}</li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted">{UNVALIDATED}</p>
      </div>
      <div className="mt-5">
        <CopyText text={text} label="Copy comparison as text" />
      </div>
      <details className="mt-3">
        <summary className="cursor-pointer text-sm text-ink-soft">Plain-text comparison</summary>
        <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-ink-soft" data-testid="comparison-text">
          {text}
        </pre>
      </details>
      <p className="mt-8 text-sm">
        <Link href={view === "synthetic" ? "/opportunities?view=synthetic" : "/opportunities"} className="underline">
          Back to the shortlist
        </Link>
      </p>
    </div>
  );
}

export function OpportunityDetail({
  report,
  slug,
  view,
}: {
  report: OpportunityReport;
  slug: string;
  view: View;
}) {
  const a = report.assessments.find((item) => item.slug === slug);
  if (!a) {
    return (
      <p role="alert" className="panel p-4 text-sm text-ink-soft">
        No game with that slug is in this report.
      </p>
    );
  }
  const entry = report.entries.find((e) => e.canonical_game_id === a.canonical_game_id);
  return (
    <>
      <Banners report={report} view={view} />
      <header className="mt-6">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{a.name}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <ReadinessBadge readiness={a.readiness} />
          <span className="text-xs text-muted">verified readiness</span>
          {view === "synthetic" ? (
            <>
              <ReadinessBadge readiness={a.simulated_readiness} simulated />
              <span className="text-xs text-bad">not a recommendation</span>
            </>
          ) : null}
        </div>
        {entry ? <p className="mt-2 text-sm text-muted">Position {entry.position} on the verified shortlist.</p> : null}
      </header>

      <Section
        id="brief"
        title="Creator decision brief"
        note="The engine's assessment of this game, said plainly: whether to act, why, what is missing and what to check first. It decides nothing the engine did not."
      >
        <CreatorBrief brief={buildBrief(report, a, view)} />
      </Section>

      <Section id="why-now" title="Why now?">
        {a.why_now.length > 0 ? (
          <ul className="list-disc pl-5 text-sm text-ink-soft">
            {a.why_now.map((s, i) => (
              <li key={i}>{s.text}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-soft">Nothing verified says “now”. The evidence is insufficient.</p>
        )}
        {a.unconfirmed.length > 0 ? (
          <div className="mt-3">
            <p className="eyebrow">Pointing somewhere, but not counted</p>
            <ul className="mt-1 space-y-1 text-sm text-ink-soft">
              {a.unconfirmed.map((s, i) => (
                <li key={i}>
                  <StandingBadge standing={s.standing} /> {s.text}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Section>

      <Section id="changed" title="What changed?">
        {a.movements.length > 0 ? (
          <ul className="space-y-1 text-sm text-ink-soft">
            {a.movements.map((m, i) => (
              <li key={i}>
                <StandingBadge standing={m.standing} /> {movementText(m)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-soft">No demand family has a current measurement for this game.</p>
        )}
        {a.facts.disagreement || a.simulated_facts.disagreement ? (
          <p className="mt-2 text-sm text-warn">Demand families disagree: one is up while another is down.</p>
        ) : null}
      </Section>

      <Section id="events" title="What event matters?">
        {a.events.length > 0 ? (
          <ul className="space-y-1 text-sm text-ink-soft">
            {a.events.map((e) => (
              <li key={e.event_id}>
                <StandingBadge standing={e.standing} /> {e.event_type.replaceAll("_", " ")} · {e.phase} · starts{" "}
                {e.starts_at.slice(0, 10)} ({e.time_precision} precision)
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-soft">No upcoming or active event is known for this game.</p>
        )}
      </Section>

      <Section id="angles" title="What video angle is supported?">
        {a.angles.length === 0 ? (
          <p className="text-sm text-ink-soft" data-testid="angle-missing">
            Angle not yet established.
          </p>
        ) : (
          <>
            {a.angle_status !== "established" ? (
              <p className="mb-2 text-sm text-ink-soft" data-testid="angle-missing">
                Angle not yet established on verified evidence. The candidates below are not confirmed.
              </p>
            ) : null}
            <ul className="space-y-3 text-sm text-ink-soft">
              {a.angles.map((angle, i) => (
                <li key={i} className="panel p-3" data-testid="angle">
                  <p className="text-ink">
                    <StandingBadge standing={angle.standing} /> {angle.addresses}
                    {angle.about.length > 0 ? ` — about ${angle.about.join(", ")}` : ""}
                  </p>
                  <p className="mt-1">
                    Supported by {angle.references.join(", ")}. {angle.timely ? "Timely" : "Not timely"} (
                    {angle.timely_reason}). Confirmed: {angle.confirmed ? "yes" : "no"}.
                  </p>
                  <p className="mt-1 text-xs text-muted">Unverified assumptions: {angle.assumptions.join("; ")}.</p>
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>

      <Section id="coverage" title="What creator coverage was observed?">
        <p className="text-sm text-ink-soft">{a.creator_coverage}</p>
      </Section>

      <Section id="community" title="What are players discussing?">
        <p className="text-sm text-ink-soft">{a.community}</p>
      </Section>

      <Section id="unknown" title="What remains unknown?">
        <ul className="list-disc pl-5 text-sm text-ink-soft">
          {a.missing.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      </Section>

      <Section id="freshness" title="How current is the evidence?">
        <table className="w-full text-left text-sm" data-testid="freshness">
          <thead className="text-xs text-muted">
            <tr>
              <th className="py-1 pr-3">Source</th>
              <th className="py-1 pr-3">State</th>
              <th className="py-1 pr-3">Latest observation</th>
              <th className="py-1 pr-3">Origin</th>
            </tr>
          </thead>
          <tbody>
            {a.sources.map((s) => (
              <tr key={s.source} className="border-t border-line">
                <td className="py-1 pr-3 font-mono text-xs">{s.source}</td>
                <td className="py-1 pr-3">{s.state.replaceAll("_", " ")}</td>
                <td className="py-1 pr-3 font-mono text-xs">{moment(s.latest_observed_at)}</td>
                <td className="py-1 pr-3">
                  <StandingBadge standing={s.standing} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section id="wrong" title="Why might this be wrong?">
        <ul className="list-disc pl-5 text-sm text-ink-soft">
          {a.caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
          <li>{a.uncertainty}</li>
        </ul>
      </Section>
      <p className="mt-8 text-sm">
        <Link href={view === "synthetic" ? "/opportunities?view=synthetic" : "/opportunities"} className="underline">
          Back to the shortlist
        </Link>
      </p>
    </>
  );
}

export function NotShown({ loaded }: { loaded: Exclude<Loaded, { kind: "ok" }> }) {
  const heading = {
    disabled: "Private opportunity reports are not enabled here",
    absent: "No private opportunity report has been generated",
    invalid: "The private opportunity report is invalid",
    unsupported_version: "The private opportunity report's rules version is not supported",
  }[loaded.kind];
  return (
    <div role="alert" className="panel border-bad/50 p-5" data-testid={`report-${loaded.kind}`}>
      <p className="eyebrow text-bad">Nothing is shown</p>
      <h2 className="mt-2 text-lg font-semibold text-ink">{heading}</h2>
      {loaded.kind === "disabled" ? <p className="mt-2 text-sm text-ink-soft">{loaded.reason}</p> : null}
      {loaded.kind === "absent" ? (
        <p className="mt-2 text-sm text-ink-soft">
          Generate it offline with{" "}
          <code className="font-mono">
            npm run opportunities:{loaded.view === "synthetic" ? "synthetic" : "recorded"}
          </code>
          .
        </p>
      ) : null}
      {loaded.kind === "unsupported_version" ? (
        <p className="mt-2 text-sm text-ink-soft">
          Found <code className="font-mono">{loaded.version}</code>; this interface reads{" "}
          <code className="font-mono">creator_opportunity_rules_v1</code> only.
        </p>
      ) : null}
      {loaded.kind === "invalid" ? (
        <ul className="mt-3 space-y-1 font-mono text-xs text-ink-soft">
          {loaded.details.slice(0, 8).map((d, i) => (
            <li key={i}>
              <span className="text-signal">{d.path}</span> {d.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
