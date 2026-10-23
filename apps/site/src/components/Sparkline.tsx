import type { Day } from "@/lib/site";

/** Daily views as a line. A missing day breaks the line instead of dropping to zero. */
export function Sparkline({ days, label }: { days: Day[]; label: string }) {
  const values = days.map((d) => d.views);
  const known = values.filter((v): v is number => v !== null);
  if (known.length < 2) return null;
  const max = Math.max(...known);
  const min = Math.min(...known);
  const span = max - min || 1;
  const width = 300;
  const height = 36;
  const step = width / Math.max(values.length - 1, 1);
  let path = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    const x = (i * step).toFixed(1);
    const y = (height - 3 - ((v - min) / span) * (height - 6)).toFixed(1);
    path += `${pen ? "L" : "M"}${x} ${y} `;
    pen = true;
  });
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={path} fill="none" stroke="var(--secondary)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
