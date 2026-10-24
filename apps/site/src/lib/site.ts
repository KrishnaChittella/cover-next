// The shape `metaspike site-data` writes (version 6). Null always means "not measured".
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

// One reading a day, kept as a history: Steam players, Twitch viewers.
export type Daily = {
  state: "ok";
  now: number;
  daily: { day: string; value: number | null }[];
  recent: number;
  normal: number | null;
  ratio: number | null;
};

// "inapplicable": the game is not on Steam.
export type Steam = { state: "inapplicable" } | { state: "missing"; app: string; daily: [] } | (Daily & { app: string });

// "unmapped": Twitch links no category to the game's IGDB id. Lower bound: pages left unread.
export type Twitch =
  | { state: "unmapped" }
  | { state: "missing"; daily: [] }
  | (Daily & { streams_now: number | null; lower_bound: boolean });

export type Rise = { recent: number; baseline: number | null; ratio: number | null; new: boolean };

// Several games of one studio rising together; `members` are game slugs, strongest first.
export type Story = {
  slug: string;
  name: string;
  role: "developer" | "publisher";
  article: string | null;
  members: string[];
};

// Rule site_opportunity_v1: ranks among today's tracked games, 0-1. Null without both halves.
export type Opportunity = {
  rule: string;
  score: number;
  curiosity_rank: number;
  supply_gap_rank: number;
  viewers_per_stream: number;
  saturation: "Low" | "Medium" | "High";
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
  steam: Steam;
  twitch: Twitch;
  opportunity: Opportunity | null;
};

type SourceState = {
  state: "ok" | "missing" | "pending";
  day?: string;
  games?: number;
  attribution?: string;
  basis?: string;
};

export type SiteData = {
  version: string;
  generated_at: string;
  day: string;
  sources: { wikipedia: SourceState; youtube: SourceState; steam: SourceState; twitch: SourceState };
  stories: Story[];
  spotlight: string | null;
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

export function steamNow(game: Game): string {
  if (game.steam.state === "inapplicable") return "not on Steam";
  return game.steam.state === "ok" ? `${formatCount(game.steam.now)} playing` : "not read";
}

export function twitchNow(game: Game): string {
  const t = game.twitch;
  if (t.state === "unmapped") return "no Twitch category";
  if (t.state !== "ok") return "not read";
  return `${t.lower_bound ? "at least " : ""}${formatCount(t.now)} watching`;
}

/** "12.3k": compact counts for tight rails. */
export function formatShort(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function topViews(game: Game): number | null {
  return game.youtube.state === "ok" ? (game.youtube.top_videos[0]?.views ?? null) : null;
}
