// The shape `metaspike site-data` writes (version 3). Null always means "not measured".
export type Day = { day: string; views: number | null };

export type Wikipedia =
  | { state: "missing"; daily: Day[]; last_7_days: null; previous_7_days: null }
  | {
      state: "ok";
      article: string;
      daily: Day[];
      last_7_days: number | null;
      previous_7_days: number | null;
    };

export type Video = { id: string; title: string; views: number | null; published_at: string };

// Individual videos only: YouTube's policies forbid figures derived across channels.
export type YouTube = { state: "missing" } | { state: "ok"; retrieved_at: string; top_videos: Video[] };

export type Rise = { recent: number; baseline: number | null; ratio: number | null; new: boolean };

// Several games of one studio rising together; `members` are game slugs, strongest first.
export type Story = {
  slug: string;
  name: string;
  role: "developer" | "publisher";
  article: string | null;
  members: string[];
};

export type Game = {
  slug: string;
  name: string;
  qid: string;
  igdb: string | null;
  steam_app: string | null;
  pinned: boolean;
  story: { slug: string; name: string; lead: boolean } | null;
  rise: Rise | null;
  wikipedia: Wikipedia;
  youtube: YouTube;
};

type SourceState = { state: "ok" | "missing" | "pending"; day?: string; games?: number };

export type SiteData = {
  version: string;
  generated_at: string;
  day: string;
  sources: { wikipedia: SourceState; youtube: SourceState; steam: SourceState; twitch: SourceState };
  stories: Story[];
  games: Game[];
};

/** A story member other than its lead folds into the story card; pinned games never fold. */
export function folded(game: Game): boolean {
  return game.story !== null && !game.story.lead && !game.pinned;
}

export type Trend = "rising" | "steady" | "cooling" | "new" | "unknown";

/** Recent 3 days against the game's own 28-day normal. Deliberately wide: x1.25 / x0.8. */
export function trendOf(game: Game): Trend {
  const rise = game.rise;
  if (!rise) return "unknown";
  if (rise.ratio === null) return rise.new ? "new" : "unknown";
  if (rise.ratio >= 1.25) return "rising";
  if (rise.ratio <= 0.8) return "cooling";
  return "steady";
}

export const TREND_LABEL: Record<Trend, string> = {
  rising: "Rising",
  steady: "Steady",
  cooling: "Cooling",
  new: "New",
  unknown: "No reading",
};

const whole = new Intl.NumberFormat("en-US");
export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? "not measured" : whole.format(value);
}

export function formatRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return "-";
  return ratio >= 10 ? `×${Math.round(ratio)}` : `×${ratio.toFixed(1)}`;
}

/** One plain sentence from real numbers. */
export function trigger(game: Game): string {
  const rise = game.rise;
  if (rise?.ratio != null && rise.baseline != null) {
    const level = rise.ratio >= 1 ? `up ${formatRatio(rise.ratio)} on normal` : `below normal (${formatRatio(rise.ratio)})`;
    return `Wikipedia views ${level}: ${formatCount(rise.recent)} a day, usually ${formatCount(rise.baseline)}`;
  }
  if (rise?.new) return `Newly noticed: ${formatCount(rise.recent)} Wikipedia views a day, no normal level yet`;
  const top = game.youtube.state === "ok" ? game.youtube.top_videos[0] : undefined;
  if (top) return `Most-watched video this week: ${top.title}`;
  return "No reading this week";
}

export function formatDay(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

export function formatUpdated(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

/** Biggest rise first; games with no normal level yet by volume; no reading last. */
export function byRise(a: Game, b: Game): number {
  const ra = a.rise?.ratio ?? null;
  const rb = b.rise?.ratio ?? null;
  if (ra === null && rb === null) return (b.rise?.recent ?? -1) - (a.rise?.recent ?? -1);
  if (ra === null) return 1;
  if (rb === null) return -1;
  return rb - ra;
}

export function topViews(game: Game): number | null {
  return game.youtube.state === "ok" ? (game.youtube.top_videos[0]?.views ?? null) : null;
}
