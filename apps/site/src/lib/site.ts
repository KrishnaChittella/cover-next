// The shape `metaspike site-data` writes. Null always means "not measured", never zero.
export type Day = { day: string; views: number | null };

export type Wikipedia = {
  state: "ok" | "missing";
  daily: Day[];
  last_7_days: number | null;
  previous_7_days: number | null;
};

export type Video = { id: string; title: string; views: number | null; published_at: string };

// Individual videos only: YouTube's policies forbid figures derived across channels.
export type YouTube =
  | { state: "missing" }
  | { state: "ok"; retrieved_at: string; top_videos: Video[] };

export type Game = { slug: string; name: string; wikipedia: Wikipedia; youtube: YouTube };
export type SiteData = { version: string; generated_at: string; games: Game[] };

export type Trend = "rising" | "steady" | "cooling" | "unknown";

/** Last 7 days against the 7 before. Thresholds are deliberately wide: +25% / -20%. */
export function trendOf(w: Wikipedia): { trend: Trend; change: number | null } {
  if (w.last_7_days === null || w.previous_7_days === null || w.previous_7_days === 0) {
    return { trend: "unknown", change: null };
  }
  const change = w.last_7_days / w.previous_7_days - 1;
  const trend = change >= 0.25 ? "rising" : change <= -0.2 ? "cooling" : "steady";
  return { trend, change };
}

const whole = new Intl.NumberFormat("en-US");
export function formatCount(value: number | null): string {
  return value === null ? "not measured" : whole.format(value);
}

export function formatChange(change: number | null): string {
  if (change === null) return "no comparison";
  const percent = Math.round(change * 100);
  return `${percent > 0 ? "+" : ""}${percent}% vs the week before`;
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
