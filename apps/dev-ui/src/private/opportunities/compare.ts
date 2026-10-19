// Comparing two or three games from one validated report. Server-side.
//
// Nothing is ranked, scored or decided here. Games are shown in the engine's own order
// for the chosen basis, at the engine's own positions; a tie the engine called a tie is
// a tie. Each game's content is its creator brief (brief.ts), so the words, the
// abstentions, the labels and the path hygiene are the brief's.
import { type Basis, type Brief, buildBrief, labelled, READINESS_WORDS, STANDING_LABEL, SYNTHETIC_WARNING, UNVALIDATED, moment } from "./brief";
import type { View } from "./loader";
import type { OpportunityReport, ShortlistEntry } from "./types";

export const MIN_GAMES = 2;
export const MAX_GAMES = 3;
export const NO_GENUINE_OPPORTUNITY =
  "No real creator opportunity is established for these games by the verified evidence.";
export const ORDER_NOTE =
  "Listed in the engine's own order. A higher position is not a predicted video outcome, and a tie ordered by id means nothing.";
const TIE = "indistinguishable on the evidence";
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export type Selection = { kind: "ok"; slugs: string[] } | { kind: "invalid"; message: string };

/** The games asked for, checked against the report. Refuses rather than guesses. */
export function parseSelection(raw: string | string[] | undefined, report: OpportunityReport): Selection {
  const asked = (Array.isArray(raw) ? raw : raw === undefined ? [] : [raw]).flatMap((value) => value.split(","));
  const values = asked.map((value) => value.trim()).filter((value) => value.length > 0);
  if (values.length > MAX_GAMES) {
    return { kind: "invalid", message: `Choose at most ${MAX_GAMES} games to compare (${values.length} were chosen).` };
  }
  if (values.length < MIN_GAMES) {
    return { kind: "invalid", message: `Choose ${MIN_GAMES} or ${MAX_GAMES} games to compare.` };
  }
  const known = new Set(report.assessments.map((a) => a.slug));
  const seen = new Set<string>();
  for (const value of values) {
    const shown = SLUG.test(value) && value.length <= 80 ? `“${value}”` : "A selection";
    if (!SLUG.test(value) || !known.has(value)) {
      return { kind: "invalid", message: `${shown} is not a game in this report.` };
    }
    if (seen.has(value)) {
      return { kind: "invalid", message: `${shown} is chosen more than once.` };
    }
    seen.add(value);
  }
  return { kind: "ok", slugs: values };
}

export interface Column {
  brief: Brief;
  entry: ShortlistEntry;
  // Games in the same tie group are indistinguishable in the engine's order.
  tieGroup: number;
}

export interface Comparison {
  basis: Basis;
  synthetic: boolean;
  asOf: string;
  rulesVersion: string;
  columns: Column[];
  ties: string[][];
  noGenuineOpportunity: boolean;
  questions: string[];
}

function tieGroups(entries: ShortlistEntry[]): Map<string, number> {
  const groups = new Map<string, number>();
  let group = 0;
  entries.forEach((entry, index) => {
    if (index > 0 && !(entries[index - 1]!.ahead_of_next ?? "").startsWith(TIE)) group += 1;
    groups.set(entry.canonical_game_id, group);
  });
  return groups;
}

export function buildComparison(report: OpportunityReport, slugs: string[], view: View): Comparison {
  const basis: Basis = view === "synthetic" ? "simulated" : "verified";
  const entries = basis === "simulated" ? report.simulated_entries : report.entries;
  const groups = tieGroups(entries);
  const chosen = new Set(slugs);
  const columns: Column[] = entries
    .filter((entry) => chosen.has(entry.slug))
    .map((entry) => {
      const a = report.assessments.find((x) => x.canonical_game_id === entry.canonical_game_id)!;
      return { brief: buildBrief(report, a, view), entry, tieGroup: groups.get(entry.canonical_game_id)! };
    });
  const byGroup = new Map<number, string[]>();
  for (const c of columns) byGroup.set(c.tieGroup, [...(byGroup.get(c.tieGroup) ?? []), c.brief.game]);
  const questions = Array.from(new Set(columns.flatMap((c) => c.brief.confirmFirst)));
  return {
    basis,
    synthetic: basis === "simulated",
    asOf: report.as_of,
    rulesVersion: report.rules_version,
    columns,
    ties: [...byGroup.values()].filter((names) => names.length > 1),
    noGenuineOpportunity: columns.every((c) => c.brief.verifiedReadiness === "insufficient_evidence"),
    questions,
  };
}

/** The comparison as plain text. Warnings open and close a simulated one; nothing is HTML. */
export function comparisonText(c: Comparison): string {
  const lines: string[] = [];
  if (c.synthetic) lines.push(`*** ${SYNTHETIC_WARNING} ***`, "");
  lines.push(
    `MetaSpike creator opportunity comparison — ${c.columns.map((col) => col.brief.game).join(" vs ")}`,
    `Assessed as of ${moment(c.asOf)} with ${c.rulesVersion}.`,
    `Basis: ${c.synthetic ? "SIMULATED from synthetic and unverified evidence" : "verified evidence only"}.`,
    ORDER_NOTE,
  );
  if (c.noGenuineOpportunity) lines.push(NO_GENUINE_OPPORTUNITY);
  for (const names of c.ties) lines.push(`Tied in the engine's order (ordered by id only, which means nothing): ${names.join(", ")}.`);
  for (const col of c.columns) {
    const b = col.brief;
    const block = (title: string, body: string[]) => lines.push(`  ${title}:`, ...(body.length ? body.map((l) => `    ${l}`) : ["    none"]));
    lines.push(
      "",
      `== ${col.entry.position}. ${b.game} ==`,
      `  Readiness: ${c.synthetic ? "SIMULATED " : ""}${READINESS_WORDS[b.readiness]}${
        c.synthetic ? ` (verified readiness: ${READINESS_WORDS[b.verifiedReadiness]})` : ""
      }; position ${col.entry.position} in the engine's order.`,
      `  Decision: ${b.decision}`,
    );
    block("Why now (counted)", b.whyNow.map(labelled));
    block("Leads, not counted", b.notCounted.map(labelled));
    block("What changed", b.changed.map(labelled));
    block("Video angle", [b.angleNote, ...b.angles.map(labelled)]);
    block("Creator coverage", b.creatorSupply);
    block("Community", b.community);
    block("Events", b.events.map((e) => `${labelled(e)}${e.counted ? "" : " — lead, not counted"}`));
    block("Counterevidence", b.counterevidence.map(labelled));
    block("Not known", b.missing);
    block("Freshness", b.freshness.map((f) => `${f.source}: ${f.state}; latest ${f.latest}; ${STANDING_LABEL[f.standing]}`));
    block("Sources", b.references);
  }
  lines.push("", "QUESTIONS TO ANSWER BEFORE CHOOSING", ...c.questions.map((q) => `- ${q}`));
  lines.push("", UNVALIDATED, "Private development only. Not published.");
  if (c.synthetic) lines.push("", `*** ${SYNTHETIC_WARNING} ***`);
  return lines.join("\n") + "\n";
}
