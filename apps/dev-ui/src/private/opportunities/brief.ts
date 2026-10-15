// The creator decision brief: one game's validated assessment, said plainly. Server-side.
//
// Nothing here is decided. Readiness, facts, order, statements, angles and caveats are
// the Python engine's (`creator_opportunity_rules_v1`); this file only chooses words for
// them. It computes no score, no readiness and no rank, and it never fills a gap:
// missing evidence is listed as missing, and an empty or partial creator sample is never
// read as low competition.
//
// Two bases, never mixed:
//   verified    the engine's real readiness, from verified evidence only (the default)
//   simulated   the synthetic view's simulated readiness, which counts invented and
//               unverified evidence as if it were real. Every simulated brief, on screen
//               and in its text, carries SYNTHETIC_WARNING.
import type { View } from "./loader";
import type { OpportunityAssessment, OpportunityReport, Readiness, Standing } from "./types";

export const SYNTHETIC_WARNING = "SYNTHETIC DEMONSTRATION — NOT A REAL RECOMMENDATION";
export const UNVALIDATED =
  "The rubric's thresholds are unvalidated: no historical evaluation has shown they mark real opportunities. Nothing here is a probability.";

export type Basis = "verified" | "simulated";

export interface BriefItem {
  text: string;
  // Who stands behind it. Anything but "verified" is labelled wherever it is shown.
  standing: Standing | null;
  references: string[];
}

export interface Brief {
  basis: Basis;
  synthetic: boolean;
  game: string;
  slug: string;
  asOf: string;
  rulesVersion: string;
  readiness: Readiness;
  verifiedReadiness: Readiness;
  position: number | null;
  decision: string;
  decisionDetail: string[];
  conditions: { label: string; met: boolean }[];
  changed: BriefItem[];
  whyNow: BriefItem[];
  notCounted: BriefItem[];
  angles: BriefItem[];
  angleNote: string;
  creatorSupply: string[];
  community: string[];
  counterevidence: BriefItem[];
  freshness: { source: string; state: string; latest: string; standing: Standing }[];
  missing: string[];
  confirmFirst: string[];
  references: string[];
  uncertainty: string;
  notices: string[];
}

export const READINESS_WORDS: Record<Readiness, string> = {
  evidence_supported_candidate: "Evidence-supported candidate",
  provisional_watchlist: "Watchlist (provisional)",
  insufficient_evidence: "Insufficient evidence",
};

const STANDING_LABEL: Record<Standing, string> = {
  verified: "VERIFIED",
  unverified: "UNVERIFIED",
  synthetic: "SYNTHETIC",
  none: "NO INPUT",
};

// Local paths never leave the private report, even if one found its way into a string.
const PATH = /(?:\/(?:Users|home|private|var|tmp|opt|mnt|Volumes)\/[^\s,;)]+|[A-Za-z]:\\[^\s,;)]+)/g;

export function scrub(text: string): string {
  return text.replace(PATH, "[local path removed]");
}

export function moment(value: string | null): string {
  if (value === null) return "—";
  return `${value.replace("T", " ").replace(/:\d\d(\.\d+)?(Z|\+00:00)$/, "")} UTC`;
}

function item(text: string, standing: Standing | null, references: string[] = []): BriefItem {
  return { text: scrub(text), standing, references: references.map(scrub) };
}

function counts(standing: Standing, basis: Basis): boolean {
  return basis === "verified" ? standing === "verified" : standing !== "none";
}

function decision(readiness: Readiness, basis: Basis): string {
  const simulated = basis === "simulated" ? "SIMULATED — " : "";
  switch (readiness) {
    case "evidence_supported_candidate":
      return `${simulated}The rubric supports further creator consideration of this game. It is not a guarantee that a video will perform.`;
    case "provisional_watchlist":
      return `${simulated}Watchlist — not a recommendation. Something points to "now", but the rubric's other conditions are not met.`;
    case "insufficient_evidence":
      return `${simulated}No recommendation. MetaSpike abstains: nothing counted says "now" for this game.`;
  }
}

export function buildBrief(report: OpportunityReport, a: OpportunityAssessment, view: View): Brief {
  const basis: Basis = view === "synthetic" ? "simulated" : "verified";
  const facts = basis === "simulated" ? a.simulated_facts : a.facts;
  const readiness = basis === "simulated" ? a.simulated_readiness : a.readiness;
  const entries = basis === "simulated" ? report.simulated_entries : report.entries;
  const position = entries.find((e) => e.canonical_game_id === a.canonical_game_id)?.position ?? null;

  const conditions = [
    {
      label: `Attention rising against its own history${facts.moving_up.length ? ` (${facts.moving_up.join(", ").replaceAll("_", " ")})` : ""}`,
      met: facts.moving_up.length > 0,
    },
    { label: "A specific, timely video angle", met: facts.angle },
    { label: "A complete creator-coverage sample", met: facts.coverage_known },
    { label: "No disagreement between demand families", met: !facts.disagreement },
  ];

  const detail: string[] = [];
  if (readiness === "evidence_supported_candidate") {
    detail.push(
      "Every rubric condition below is met on the evidence counted. Limits: the thresholds are unvalidated, a creator sample is a lower bound on competition, and nothing measures how a video would perform.",
    );
  } else if (readiness === "provisional_watchlist") {
    const unmet = conditions.filter((c) => !c.met).map((c) => c.label.toLowerCase());
    detail.push(`Not met: ${unmet.join("; ") || "—"}. Treat this as a reason to look again, not to publish.`);
  } else {
    detail.push(
      basis === "verified"
        ? "No verified demand family moved past the rubric's threshold and no verified event is upcoming or active. Missing evidence is not negative evidence: it means MetaSpike cannot say."
        : "Even counting invented evidence, nothing says “now”.",
    );
  }

  const changed = a.movements.map((m) =>
    item(
      `${m.family.replaceAll("_", " ")}: ${m.direction}${
        m.log_lift === null || m.robust_z === null
          ? " (no measurement)"
          : ` (log lift ${m.log_lift.toPrecision(3)}, robust z ${m.robust_z.toPrecision(3)})`
      }, observed through ${moment(m.observed_through)}`,
      m.standing,
      [m.source],
    ),
  );

  const statements = basis === "simulated" ? [...a.why_now, ...a.unconfirmed] : a.why_now;
  const whyNow = statements.map((s) => item(s.text, s.standing, s.references));
  const notCounted =
    basis === "verified" ? a.unconfirmed.map((s) => item(`${s.text} (not counted)`, s.standing, s.references)) : [];
  for (const e of a.events) {
    if (!counts(e.standing, basis)) {
      notCounted.push(
        item(
          `${e.event_type.replaceAll("_", " ")} (${e.phase}), starting ${e.starts_at.slice(0, 10)} — not counted as a fact`,
          e.standing,
          [e.event_id],
        ),
      );
    }
  }

  const supported = a.angles.filter((angle) => angle.timely && counts(angle.standing, basis));
  const angles = (facts.angle ? supported : a.angles).map((angle) =>
    item(
      `${angle.addresses}${angle.about.length ? ` — about ${angle.about.join(", ")}` : ""}. ${
        angle.timely ? "Timely" : "Not timely"
      } (${angle.timely_reason}). Assumes: ${angle.assumptions.join("; ")}.`,
      angle.standing,
      angle.references,
    ),
  );
  const angleNote = facts.angle
    ? basis === "simulated"
      ? "A simulated angle: the evidence behind it is invented or unverified."
      : "Supported by verified evidence."
    : a.angles.length > 0
      ? "No video angle is established. The candidates below are leads only, not supported angles."
      : "No video angle is established, and no candidate angle exists.";

  const coverage = scrub(a.creator_coverage);
  const creatorSupply = [coverage];
  if (coverage.startsWith("No creator-supply input")) {
    creatorSupply.push("No creator sample was taken. That is not low competition: competition is simply unknown.");
  } else if (!facts.coverage_known) {
    creatorSupply.push("The sample is incomplete or not counted, so it cannot describe competition.");
  }
  creatorSupply.push("Even a complete sample is a lower bound: total creator competition is unknown.");

  const communityText = scrub(a.community);
  const community = [communityText];
  if (communityText.startsWith("No community input")) {
    community.push("No discussion was sampled. That is not low interest: interest is simply unknown.");
  }

  const counterevidence: BriefItem[] = a.movements
    .filter((m) => m.direction === "down")
    .map((m) => item(`${m.family.replaceAll("_", " ")} is DOWN against its own history`, m.standing, [m.source]));
  if (facts.disagreement || a.facts.disagreement) {
    counterevidence.push(item("Demand families disagree: one is up while another is down.", null));
  }
  for (const caveat of a.caveats) counterevidence.push(item(caveat, null));

  const confirmFirst: string[] = [];
  if (!facts.moving_up.length) confirmFirst.push("That audience attention is actually rising: no counted demand family shows it.");
  if (!facts.angle) confirmFirst.push("A specific, timely angle: none is established on the evidence counted.");
  if (!facts.coverage_known)
    confirmFirst.push("How many creators already cover it: MetaSpike's sample is missing, incomplete or not counted.");
  if (facts.disagreement) confirmFirst.push("Why the demand families disagree.");
  if (a.sources.some((s) => s.standing === "unverified" || s.standing === "synthetic"))
    confirmFirst.push("Anything marked UNVERIFIED or SYNTHETIC: treat it as a lead, never as a fact.");
  confirmFirst.push("That the topic suits your channel: nothing here measures that.");

  const references = Array.from(
    new Set(
      [
        ...a.sources.filter((s) => s.standing !== "none").map((s) => s.source),
        ...whyNow.flatMap((i) => i.references),
        ...angles.flatMap((i) => i.references),
        ...notCounted.flatMap((i) => i.references),
      ].map(scrub),
    ),
  );

  const notices: string[] = [];
  if (basis === "simulated") {
    notices.push(SYNTHETIC_WARNING);
    notices.push("Simulated readiness counts invented and unverified evidence as if it were real.");
  }
  if (basis === "verified" && report.contains_synthetic)
    notices.push("This report holds synthetic inputs; they count for nothing in this brief.");
  if (report.contains_unverified || a.sources.some((s) => s.standing === "unverified"))
    notices.push("Some inputs are UNVERIFIED: shown and labelled, never counted as verified.");

  return {
    basis,
    synthetic: basis === "simulated",
    game: scrub(a.name),
    slug: a.slug,
    asOf: a.as_of,
    rulesVersion: a.rules_version,
    readiness,
    verifiedReadiness: a.readiness,
    position,
    decision: decision(readiness, basis),
    decisionDetail: detail,
    conditions,
    changed,
    whyNow,
    notCounted,
    angles,
    angleNote,
    creatorSupply,
    community,
    counterevidence,
    freshness: a.sources.map((s) => ({
      source: s.source,
      state: s.state.replaceAll("_", " "),
      latest: moment(s.latest_observed_at),
      standing: s.standing,
    })),
    missing: a.missing.map(scrub),
    confirmFirst,
    references,
    uncertainty: scrub(a.uncertainty),
    notices,
  };
}

function labelled(i: BriefItem): string {
  const label = i.standing === null || i.standing === "verified" ? "" : `[${STANDING_LABEL[i.standing]}] `;
  const refs = i.references.length ? ` (source: ${i.references.join(", ")})` : "";
  return `- ${label}${i.text}${refs}`;
}

/** The brief as plain text, for copying. Warnings open and close it; nothing is HTML. */
export function briefText(b: Brief): string {
  const lines: string[] = [];
  const section = (title: string, body: string[]) => {
    lines.push("", title.toUpperCase(), ...(body.length ? body : ["- none"]));
  };
  if (b.synthetic) lines.push(`*** ${SYNTHETIC_WARNING} ***`, "");
  lines.push(
    `MetaSpike creator decision brief — ${b.game}`,
    `Assessed as of ${moment(b.asOf)} with ${b.rulesVersion}.`,
    `Basis: ${b.basis === "verified" ? "verified evidence only" : "SIMULATED from synthetic and unverified evidence"}.`,
    `Readiness: ${b.basis === "simulated" ? "SIMULATED " : ""}${READINESS_WORDS[b.readiness]}${
      b.basis === "simulated" ? ` (verified readiness: ${READINESS_WORDS[b.verifiedReadiness]})` : ""
    }${b.position === null ? "" : `; position ${b.position} in the engine's order`}.`,
  );
  for (const n of b.notices) if (n !== SYNTHETIC_WARNING) lines.push(`NOTE: ${n}`);
  section("Decision", [b.decision, ...b.decisionDetail]);
  section(
    "Rubric conditions (the engine's facts)",
    b.conditions.map((c) => `- [${c.met ? "met" : "not met"}] ${c.label}`),
  );
  section("What changed", b.changed.map(labelled));
  section(b.basis === "simulated" ? "Why now (simulated)" : "Why now (verified)", b.whyNow.map(labelled));
  if (b.notCounted.length) section("Pointing somewhere, but not counted", b.notCounted.map(labelled));
  section("Video angle", [b.angleNote, ...b.angles.map(labelled)]);
  section("Creator supply", b.creatorSupply.map((t) => `- ${t}`));
  section("Community", b.community.map((t) => `- ${t}`));
  section("Counterevidence and caveats", b.counterevidence.map(labelled));
  section(
    "Freshness",
    b.freshness.map((f) => `- ${f.source}: ${f.state}; latest ${f.latest}; ${STANDING_LABEL[f.standing]}`),
  );
  section("Not known", b.missing.map((m) => `- ${m}`));
  section("Confirm before acting", b.confirmFirst.map((c) => `- ${c}`));
  section("Uncertainty", [b.uncertainty, UNVALIDATED]);
  section("Sources referenced", b.references.map((r) => `- ${r}`));
  lines.push("", "Private development only. Not published.");
  if (b.synthetic) lines.push("", `*** ${SYNTHETIC_WARNING} ***`);
  return lines.join("\n") + "\n";
}
