"use client";

// One time-series chart for one HistorySeries. A page with several series renders
// several of these, so a new source or metric needs no change here.
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";

import type { HistorySeries } from "@/contract";
import { formatCount, formatDay, humanize } from "@/lib/format";
import { RAW_SERIES_NAME, ROLLING_SERIES_NAME, chartSeries, densify, summarise } from "@/lib/history";

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const INK = "#e8eaee";
const MUTED = "#8a909c";
const LINE = "#23272f";
const RAW = "#7f8794";
const ROLLING = "#e0a83c";

interface TooltipItem {
  seriesName?: string;
  marker?: string;
  value?: [string, number | null];
}

function buildOption(series: HistorySeries) {
  const [raw, rolling] = chartSeries(series);
  return {
    animation: false,
    textStyle: { fontFamily: "ui-sans-serif, system-ui, sans-serif", color: MUTED },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 16,
      itemHeight: 2,
      icon: "rect",
      textStyle: { color: MUTED, fontSize: 12 },
      inactiveColor: "#454b56",
    },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: "#171a20",
      borderColor: "#343a45",
      padding: [8, 10],
      textStyle: { color: INK, fontSize: 12 },
      axisPointer: { type: "line", lineStyle: { color: "#454b56" } },
      formatter: (items: TooltipItem[]) => {
        const day = items[0]?.value?.[0];
        const lines = items.map((item) => {
          const value = item.value?.[1];
          const shown = value === null || value === undefined ? "no observation" : formatCount(value);
          return `${item.marker ?? ""}${item.seriesName ?? ""}: <strong>${shown}</strong>`;
        });
        return [`<div style="margin-bottom:4px;color:${MUTED}">${formatDay(day)}</div>`, ...lines].join("<br/>");
      },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: LINE } },
      axisTick: { show: false },
      axisLabel: { color: MUTED, fontSize: 11, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      // Daily volumes are read against their own level, not against zero.
      scale: true,
      axisLabel: { color: MUTED, fontSize: 11, formatter: (value: number) => formatCount(value) },
      splitLine: { lineStyle: { color: LINE } },
    },
    series: [
      {
        ...raw,
        type: "line",
        showSymbol: true,
        symbol: "circle",
        symbolSize: 3,
        lineStyle: { width: 1, color: RAW },
        itemStyle: { color: RAW },
        emphasis: { disabled: true },
      },
      {
        ...rolling,
        type: "line",
        showSymbol: false,
        lineStyle: { width: 2, color: ROLLING },
        itemStyle: { color: ROLLING },
        emphasis: { disabled: true },
      },
    ],
  };
}

export function HistoryChart({ series, gameName }: { series: HistorySeries; gameName: string }) {
  const container = useRef<HTMLDivElement>(null);
  const summary = useMemo(() => summarise(series), [series]);
  const dense = useMemo(() => densify(series), [series]);
  const title = `${humanize(series.metric)}, ${series.granularity}`;
  const empty = summary.observedDays === 0;

  useEffect(() => {
    const element = container.current;
    if (!element || empty) return;
    const chart = echarts.init(element, undefined, { renderer: "canvas" });
    chart.setOption(buildOption(series));
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [series, empty]);

  const description = empty
    ? `No observed value for ${gameName} in this series.`
    : `${title} for ${gameName} from ${formatDay(summary.firstDay)} to ${formatDay(summary.lastDay)}: ` +
      `${summary.observedDays} of ${summary.calendarDays} days observed` +
      (summary.missingDays > 0 ? `, ${summary.missingDays} missing and shown as gaps` : "") +
      `. Latest ${formatCount(summary.latest?.value)} on ${formatDay(summary.latest?.day)}; ` +
      `highest ${formatCount(summary.peak?.value)} on ${formatDay(summary.peak?.day)}; ` +
      `lowest ${formatCount(summary.low?.value)} on ${formatDay(summary.low?.day)}.`;

  return (
    <figure className="panel p-4 sm:p-5">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <span className="font-medium text-ink">{title}</span>
        <span className="text-xs text-muted">Source: {humanize(series.source)}</span>
      </figcaption>

      {empty ? (
        <p className="flex h-72 items-center justify-center rounded bg-raised text-sm text-muted">
          No observed value in this series.
        </p>
      ) : (
        // A fixed height, so the page does not move when the chart draws.
        <div ref={container} role="img" aria-label={description} className="h-72 w-full" />
      )}

      <p className="mt-3 text-sm text-ink-soft">{description}</p>
      <p className="mt-1 text-xs text-muted">
        {RAW_SERIES_NAME} and {ROLLING_SERIES_NAME.toLowerCase()} exactly as published. A day without an observation
        is a gap: nothing is interpolated and nothing is drawn as zero.
        {series.attribution ? ` ${series.attribution}` : ""}
      </p>

      {dense.days.length > 0 ? (
        <details className="mt-3">
          <summary className="cursor-pointer rounded text-sm text-ink-soft hover:text-ink">
            Show the {dense.days.length} daily values as a table
          </summary>
          <div className="mt-3 max-h-80 overflow-auto rounded border border-line">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                {title} for {gameName}, by day
              </caption>
              <thead className="sticky top-0 bg-raised text-xs text-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    Day (UTC)
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {RAW_SERIES_NAME}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {ROLLING_SERIES_NAME}
                  </th>
                </tr>
              </thead>
              <tbody>
                {dense.days.map((day, index) => {
                  const value = dense.values[index]?.[1] ?? null;
                  const mean = dense.rolling[index]?.[1] ?? null;
                  return (
                    <tr key={day} className="border-t border-line">
                      <th scope="row" className="num px-3 py-1.5 text-left font-normal text-ink-soft">
                        {formatDay(day)}
                      </th>
                      <td className="num px-3 py-1.5 text-right font-mono">
                        {value === null ? <span className="text-faint">no observation</span> : formatCount(value)}
                      </td>
                      <td className="num px-3 py-1.5 text-right font-mono">
                        {mean === null ? <span className="text-faint">not available</span> : formatCount(mean)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </figure>
  );
}
