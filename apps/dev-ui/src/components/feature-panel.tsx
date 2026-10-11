import type { ReactNode } from "react";

import type { FeaturesView } from "@/contract";
import { ABSENT, formatCount, formatDay, formatDecimal, formatPercentile, formatStaleness, humanize } from "@/lib/format";

import { Trend } from "./ui";

interface Row {
  key: string;
  label: string;
  hint: string;
  value: ReactNode;
  absent: boolean;
}

function absentOr(value: number | null | undefined, render: (value: number) => ReactNode): [ReactNode, boolean] {
  return value === null || value === undefined ? [ABSENT, true] : [render(value), false];
}

function row(key: string, label: string, hint: string, [value, absent]: [ReactNode, boolean]): Row {
  return { key, label, hint, value, absent };
}

const views = (value: number) => (
  <>
    {formatCount(value)} <span className="text-xs text-muted">views/day</span>
  </>
);

/** Rows for the feature table. A feature the run could not compute is absent, not zero. */
export function featureRows(features: FeaturesView): { group: string; rows: Row[] }[] {
  return [
    {
      group: "Level",
      rows: [
        row("latest_views", "Latest views", "Views on the latest observed day.", absentOr(features.latest_views, views)),
        row("avg_3d", "3-day average", "Mean of the observed days among the latest three.", absentOr(features.avg_3d, views)),
        row("avg_7d", "7-day average", "Mean of the observed days among the latest seven.", absentOr(features.avg_7d, views)),
        row(
          "prior_avg_3d",
          "Prior 3-day average",
          "Mean of the three days before the latest three.",
          absentOr(features.prior_avg_3d, views),
        ),
        row(
          "baseline_median_28d",
          "28-day baseline median",
          "Median of the 28-day baseline window, which ends a week before the latest day. The game's normal level.",
          absentOr(features.baseline_median_28d, views),
        ),
      ],
    },
    {
      group: "Change",
      rows: [
        row(
          "lift_pct",
          "Lift",
          "3-day average against the 28-day baseline median.",
          absentOr(features.lift_pct, (value) => <Trend value={value} />),
        ),
        row(
          "lift_7d_pct",
          "7-day lift",
          "7-day average against the 28-day baseline median.",
          absentOr(features.lift_7d_pct, (value) => <Trend value={value} />),
        ),
        row(
          "velocity_pct",
          "Velocity",
          "Latest three days against the three days before them.",
          absentOr(features.velocity_pct, (value) => <Trend value={value} />),
        ),
        row("consecutive_rising_days", "Consecutive rising days", "Days in a row that rose, ending on the latest day.", [
          formatCount(features.consecutive_rising_days ?? 0),
          false,
        ]),
      ],
    },
    {
      group: "How unusual",
      rows: [
        row(
          "robust_z_score",
          "Robust z-score",
          features.robust_scale_method
            ? `Distance from the baseline median in robust deviations (${features.robust_scale_method === "mad" ? "median absolute deviation" : "mean absolute deviation"}).`
            : "Distance from the baseline median in robust deviations.",
          absentOr(features.robust_z_score, (value) => formatDecimal(value)),
        ),
        row(
          "history_percentile",
          "History percentile",
          "Where the latest day ranks among all earlier observed days in the window.",
          absentOr(features.history_percentile, (value) => (
            <>
              {formatPercentile(value)} <span className="text-xs text-muted">of 100</span>
            </>
          )),
        ),
      ],
    },
    {
      group: "Data behind it",
      rows: [
        row("staleness_days", "Staleness", "How far the latest observation trails the day it was due.", [
          features.staleness_days === null || features.staleness_days === undefined
            ? ABSENT
            : formatStaleness(features.staleness_days),
          features.staleness_days === null || features.staleness_days === undefined,
        ]),
        row("observed_days", "Observed days", "Days with an observation in the window.", [
          <>
            {formatCount(features.observed_days)} <span className="text-xs text-muted">of {formatCount(features.window_days)}</span>
          </>,
          false,
        ]),
        row("missing_days", "Missing days", "Days with no observation between the first observed day and the latest. Excluded from every window, never counted as zero.", [
          formatCount(features.missing_days ?? 0),
          false,
        ]),
      ],
    },
  ];
}

export function FeaturePanel({ features }: { features: FeaturesView }) {
  const groups = featureRows(features);
  const flags = features.flags ?? [];
  return (
    <div className="panel p-5">
      <p className="text-xs text-muted">
        As of {formatDay(features.as_of_day)}. A dash means the run could not compute the feature; it does not mean
        zero.
      </p>
      <div className="mt-4 grid gap-x-10 gap-y-6 md:grid-cols-2">
        {groups.map(({ group, rows }) => (
          <div key={group}>
            <h3 className="eyebrow">{group}</h3>
            <dl className="mt-2 divide-y divide-line">
              {rows.map((item) => (
                <div key={item.key} data-feature={item.key} className="flex items-start justify-between gap-6 py-2">
                  <dt className="min-w-0">
                    <span className="text-sm text-ink">{item.label}</span>
                    <span className="block text-xs leading-snug text-muted">{item.hint}</span>
                  </dt>
                  <dd className={`num shrink-0 text-right font-mono text-sm ${item.absent ? "text-faint" : "text-ink"}`}>
                    {item.absent ? (
                      <>
                        <span aria-hidden="true">{ABSENT}</span>
                        <span className="sr-only">not available</span>
                      </>
                    ) : (
                      item.value
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
      {flags.length > 0 ? (
        <p className="mt-4 text-xs text-muted">
          Flags raised by the run: {flags.map((flag) => humanize(flag)).join("; ")}.
        </p>
      ) : null}
    </div>
  );
}
