// Small presentational pieces shared by every page. Each state they show has a word
// and a shape as well as a colour.
import type { ReactNode } from "react";

import { direction, formatSignedPercent, formatUnit, type Direction } from "@/lib/format";

export function Section({
  id,
  title,
  note,
  aside,
  children,
}: {
  id: string;
  title: string;
  note?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-title`} className="mt-10 first:mt-0">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <h2 id={`${id}-title`} className="text-base font-semibold tracking-tight text-ink">
            {title}
          </h2>
          {note ? <p className="mt-1 max-w-3xl text-sm text-muted">{note}</p> : null}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "signal";

const TONES: Record<Tone, string> = {
  neutral: "border-line-strong text-ink-soft",
  good: "border-up/40 text-up",
  warn: "border-warn/40 text-warn",
  bad: "border-bad/40 text-bad",
  info: "border-info/40 text-info",
  signal: "border-signal/50 text-signal",
};

const GLYPHS: Record<Tone, string> = {
  neutral: "",
  good: "●",
  warn: "▲",
  bad: "■",
  info: "◆",
  signal: "",
};

export function Badge({ tone = "neutral", glyph, children }: { tone?: Tone; glyph?: boolean; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap ${TONES[tone]}`}
    >
      {glyph && GLYPHS[tone] ? (
        <span aria-hidden="true" className="text-[0.6rem] leading-none">
          {GLYPHS[tone]}
        </span>
      ) : null}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  healthy: "good",
  covered: "good",
  degraded: "warn",
  not_mapped: "info",
  failed: "bad",
};

/** A source or coverage status: word, shape and colour together. */
export function StatusBadge({ status, label }: { status: string; label: string }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? "neutral"} glyph>
      {label}
    </Badge>
  );
}

const BAND_TONE: Record<string, Tone> = { low: "bad", medium: "warn", high: "good" };
const BAND_GLYPH: Record<string, string> = { low: "▁", medium: "▄", high: "█" };

export function BandBadge({ band, label }: { band: string; label: string }) {
  return (
    <Badge tone={BAND_TONE[band] ?? "neutral"}>
      <span aria-hidden="true" className="text-[0.6rem] leading-none">
        {BAND_GLYPH[band] ?? ""}
      </span>
      {label}
    </Badge>
  );
}

/** A value on 0 to 1 as a bar with its number. The number is the information. */
export function Meter({ label, value, hint }: { label: string; value: number; hint?: string }) {
  const share = Math.min(1, Math.max(0, value));
  return (
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm text-ink-soft">{label}</span>
        <span className="num font-mono text-sm text-ink">{formatUnit(value)}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={Number(share.toFixed(4))}
        className="mt-1.5 h-1.5 overflow-hidden rounded-sm bg-raised"
      >
        <div className="h-full bg-signal" style={{ width: `${share * 100}%` }} />
      </div>
      {hint ? <p className="mt-1.5 text-xs leading-snug text-muted">{hint}</p> : null}
    </div>
  );
}

const DIRECTION: Record<Direction, { glyph: string; word: string; tone: string }> = {
  up: { glyph: "▲", word: "up", tone: "text-up" },
  down: { glyph: "▼", word: "down", tone: "text-down" },
  flat: { glyph: "■", word: "unchanged", tone: "text-muted" },
  unknown: { glyph: "", word: "not available", tone: "text-faint" },
};

/** A signed percentage with an arrow. Absent stays absent; it is never shown as 0%. */
export function Trend({ value }: { value: number | null | undefined }) {
  const way = DIRECTION[direction(value)];
  return (
    <span className={`num inline-flex items-baseline gap-1 font-mono ${way.tone}`}>
      {way.glyph ? (
        <span aria-hidden="true" className="text-[0.6rem]">
          {way.glyph}
        </span>
      ) : null}
      <span>{formatSignedPercent(value)}</span>
      <span className="sr-only">({way.word})</span>
    </span>
  );
}

export function Stat({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd className="mt-1 text-sm text-ink">{children}</dd>
      {hint ? <dd className="mt-0.5 text-xs text-muted">{hint}</dd> : null}
    </div>
  );
}

export function PageTitle({ title, lede, children }: { title: string; lede?: ReactNode; children?: ReactNode }) {
  return (
    <header className="mb-8">
      <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
      {lede ? <p className="mt-2 max-w-3xl text-ink-soft">{lede}</p> : null}
      {children}
    </header>
  );
}
