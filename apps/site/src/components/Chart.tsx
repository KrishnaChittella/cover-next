import { formatCount, formatDay, type Day } from "@/lib/site";

/** Daily views with the game's normal level as a dashed line. Unknown days break the line. */
export function Chart({ days, baseline, label }: { days: Day[]; baseline: number | null; label: string }) {
  const values = days.map((d) => d.views);
  const known = values.filter((v): v is number => v !== null);
  if (known.length < 2) return <p className="missing">Not enough days measured for a chart yet.</p>;
  const max = Math.max(...known, baseline ?? 0);
  const width = 600;
  const height = 180;
  const pad = 8;
  const x = (i: number) => (i * (width - 2 * pad)) / Math.max(values.length - 1, 1) + pad;
  const y = (v: number) => height - pad - (v / (max || 1)) * (height - 2 * pad);
  let line = "";
  let area = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    line += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
    pen = true;
  });
  const firstKnown = values.findIndex((v) => v !== null);
  const lastKnown = values.length - 1 - [...values].reverse().findIndex((v) => v !== null);
  if (values.every((v) => v !== null)) {
    area = `${line} L${x(lastKnown).toFixed(1)} ${height - pad} L${x(firstKnown).toFixed(1)} ${height - pad} Z`;
  }
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} preserveAspectRatio="none">
        {area && <path d={area} className="chart-area" />}
        {baseline !== null && <line x1={pad} x2={width - pad} y1={y(baseline)} y2={y(baseline)} className="chart-base" />}
        <path d={line} className="chart-line" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="chart-axis mono">
        <span>{days[0] ? formatDay(days[0].day) : ""}</span>
        <span>
          peak {formatCount(Math.max(...known))}
          {baseline !== null ? ` · normal ${formatCount(baseline)} (dashed)` : ""}
        </span>
        <span>{days.at(-1) ? formatDay(days.at(-1)!.day) : ""}</span>
      </figcaption>
    </figure>
  );
}
