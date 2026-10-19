// Turning a HistorySeries into what a chart and its text fallback need. The rule is
// that a missing day stays missing: it is never joined across and never drawn as zero.
import type { HistorySeries } from "@/contract";

export type Sample = [day: string, value: number | null];

const DAY_MS = 86_400_000;

function toUtc(day: string): number {
  return Date.parse(`${day}T00:00:00Z`);
}

function fromUtc(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export interface DenseSeries {
  days: string[];
  values: Sample[];
  rolling: Sample[];
}

/**
 * One sample for every calendar day from the first point to the last. A day the series
 * leaves out, or carries as null, becomes an explicit null so a line breaks there.
 */
export function densify(series: HistorySeries): DenseSeries {
  const byDay = new Map(series.points.map((point) => [point.day, point]));
  const sorted = [...byDay.keys()].sort();
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const dense: DenseSeries = { days: [], values: [], rolling: [] };
  if (first === undefined || last === undefined) return dense;
  for (let ms = toUtc(first); ms <= toUtc(last); ms += DAY_MS) {
    const day = fromUtc(ms);
    const point = byDay.get(day);
    dense.days.push(day);
    dense.values.push([day, point?.value ?? null]);
    dense.rolling.push([day, point?.rolling_mean_7d ?? null]);
  }
  return dense;
}

export interface HistorySummary {
  firstDay: string | null;
  lastDay: string | null;
  calendarDays: number;
  observedDays: number;
  missingDays: number;
  latest: { day: string; value: number } | null;
  peak: { day: string; value: number } | null;
  low: { day: string; value: number } | null;
}

export function summarise(series: HistorySeries): HistorySummary {
  const dense = densify(series);
  const observed = dense.values.filter((sample): sample is [string, number] => sample[1] !== null);
  const pick = (better: (a: number, b: number) => boolean) =>
    observed.reduce<{ day: string; value: number } | null>(
      (best, [day, value]) => (best === null || better(value, best.value) ? { day, value } : best),
      null,
    );
  const final = observed[observed.length - 1];
  return {
    firstDay: dense.days[0] ?? null,
    lastDay: dense.days[dense.days.length - 1] ?? null,
    calendarDays: dense.days.length,
    observedDays: observed.length,
    missingDays: dense.days.length - observed.length,
    latest: final ? { day: final[0], value: final[1] } : null,
    peak: pick((a, b) => a > b),
    low: pick((a, b) => a < b),
  };
}

export const RAW_SERIES_NAME = "Daily value";
export const ROLLING_SERIES_NAME = "7-day mean";

export interface ChartSeries {
  name: string;
  data: Sample[];
  connectNulls: false;
}

/** The two lines drawn for one series. `connectNulls` is false on both, always. */
export function chartSeries(series: HistorySeries): ChartSeries[] {
  const dense = densify(series);
  return [
    { name: RAW_SERIES_NAME, data: dense.values, connectNulls: false },
    { name: ROLLING_SERIES_NAME, data: dense.rolling, connectNulls: false },
  ];
}
