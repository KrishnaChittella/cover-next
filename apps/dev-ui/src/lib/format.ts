// Number, date and label formatting. One rule runs through all of it: a value that is
// not there is shown as absent, never as zero.
export const ABSENT = "—";

const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const twoDecimal = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function present(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** A count or a daily volume: whole numbers with thousands separators. */
export function formatCount(value: number | null | undefined): string {
  return present(value) ? integer.format(value) : ABSENT;
}

/** A score on 0 to 100, always one decimal so a column lines up. */
export function formatScore(value: number | null | undefined): string {
  return present(value) ? oneDecimal.format(value) : ABSENT;
}

/** Confidence on 0 to 100. Whole numbers stay whole; anything else gets one decimal. */
export function formatConfidence(value: number | null | undefined): string {
  if (!present(value)) return ABSENT;
  return Number.isInteger(value) ? integer.format(value) : oneDecimal.format(value);
}

/** A share of 1, as shown beside a component bar. */
export function formatUnit(value: number | null | undefined): string {
  return present(value) ? twoDecimal.format(value) : ABSENT;
}

/**
 * Event Heat on 0 to 100. Zero is a finding of its own ("a source looked and nothing
 * contributes"), so a heat that is positive but below the displayed precision is never
 * shown as 0.0: it reads "<0.1". The artifact keeps the exact number.
 */
export function formatHeat(value: number | null | undefined): string {
  if (!present(value)) return ABSENT;
  if (value > 0 && value < 0.05) return "<0.1";
  return oneDecimal.format(value);
}

/** A strength or contribution on 0 to 1. A positive value never reads as 0.00. */
export function formatShare(value: number | null | undefined): string {
  if (!present(value)) return ABSENT;
  if (value > 0 && value < 0.005) return "<0.01";
  return twoDecimal.format(value);
}

/**
 * A realized outcome held as a fraction (0.25 is 25% above the reference), shown as a
 * signed percentage. An outcome that could not be evaluated is absent, never 0%.
 */
export function formatOutcome(fraction: number | null | undefined): string {
  return present(fraction) ? formatSignedPercent(fraction * 100) : ABSENT;
}

/** A share held as a fraction, shown as a whole percentage. */
export function formatRate(fraction: number | null | undefined): string {
  return present(fraction) ? `${integer.format(Math.round(fraction * 100))}%` : ABSENT;
}

/** A correlation between -1 and 1, always signed so its direction cannot be misread. */
export function formatCorrelation(value: number | null | undefined): string {
  if (!present(value)) return ABSENT;
  const rounded = Math.round(value * 100) / 100;
  if (rounded === 0) return "0.00";
  return `${rounded > 0 ? "+" : "−"}${twoDecimal.format(Math.abs(rounded))}`;
}

/** A signed percentage change. Zero carries no sign; the minus is a real minus sign. */
export function formatSignedPercent(value: number | null | undefined): string {
  if (!present(value)) return ABSENT;
  const rounded = Math.round(value * 10) / 10;
  if (rounded === 0) return "0.0%";
  return `${rounded > 0 ? "+" : "−"}${oneDecimal.format(Math.abs(rounded))}%`;
}

export function formatPercentile(value: number | null | undefined): string {
  return present(value) ? `${oneDecimal.format(value)}` : ABSENT;
}

export function formatDecimal(value: number | null | undefined): string {
  return present(value) ? twoDecimal.format(value) : ABSENT;
}

/** Which way a change points. Used with an arrow and a word, never colour alone. */
export type Direction = "up" | "down" | "flat" | "unknown";

export function direction(value: number | null | undefined): Direction {
  if (!present(value)) return "unknown";
  const rounded = Math.round(value * 10) / 10;
  return rounded > 0 ? "up" : rounded < 0 ? "down" : "flat";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A calendar day from the contract (`YYYY-MM-DD`), which is always a UTC day. */
export function formatDay(day: string | null | undefined): string {
  if (!day) return ABSENT;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!match) return day;
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${Number(match[3])} ${month} ${match[1]}` : day;
}

/** A timestamp from the contract, shown in UTC so it reads the same everywhere. */
export function formatTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) return ABSENT;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  const iso = date.toISOString();
  return `${formatDay(iso.slice(0, 10))}, ${iso.slice(11, 16)} UTC`;
}

export function formatStaleness(days: number | null | undefined): string {
  if (!present(days)) return "no observation";
  if (days === 0) return "current";
  return `${integer.format(days)} day${days === 1 ? "" : "s"} behind`;
}

export function plural(count: number, noun: string): string {
  return `${integer.format(count)} ${noun}${count === 1 ? "" : "s"}`;
}

const LIFECYCLE: Record<string, string> = {
  PRE_RELEASE: "Pre-release",
  LAUNCH_WINDOW: "Launch window",
  RELEASED: "Released",
  EVERGREEN: "Evergreen",
  LIVE_SERVICE: "Live service",
  DORMANT: "Dormant",
};

const RELATIONSHIP: Record<string, string> = {
  MOD_ECOSYSTEM: "Mod ecosystem of",
  ONLINE_COMPONENT: "Online component of",
  SEQUEL: "Sequel to",
  REMAKE: "Remake of",
  REMASTER: "Remaster of",
  STANDALONE_EXPANSION: "Standalone expansion of",
  PACKAGING_VARIANT: "Edition of",
  COLLECTION: "Collection including",
};

const UNSCORED: Record<string, string> = {
  no_observations: "No observations",
  insufficient_history: "Not enough history",
  insufficient_recent_data: "Not enough recent data",
  zero_baseline: "Baseline is zero",
  zero_dispersion: "No variation in the baseline",
};

const COVERAGE: Record<string, string> = {
  covered: "Covered",
  failed: "Collection failed",
  not_mapped: "Not mapped",
};

/** A contract identifier as words: `wikimedia_pageviews` reads "Wikimedia pageviews". */
export function humanize(identifier: string): string {
  const words = identifier.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const lifecycleLabel = (state: string): string => LIFECYCLE[state] ?? humanize(state);
export const relationshipLabel = (type: string): string => RELATIONSHIP[type] ?? humanize(type);
export const unscoredLabel = (reason: string): string => UNSCORED[reason] ?? humanize(reason);
export const coverageLabel = (status: string): string => COVERAGE[status] ?? humanize(status);
export const bandLabel = (band: string): string => humanize(band);
