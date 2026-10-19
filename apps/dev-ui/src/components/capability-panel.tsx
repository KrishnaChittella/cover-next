// What the current artifacts contain and what they do not. No number appears beside
// anything that does not exist yet.
const AVAILABLE = [
  ["Attention anomaly", "How unusual the latest attention is against the game's own baseline."],
  ["Momentum", "Whether attention is still accelerating over the last few days."],
  ["Confidence", "How far the data supports the reading, capped by the number of sources."],
  ["Why-now explanation", "Sentences rendered from computed features by fixed templates."],
  ["Source freshness", "How current each source's latest observation is."],
] as const;

const NOT_YET = [
  [
    "Event heat",
    "An internal, uncalibrated engine exists, but no event source is collected. It is unavailable for every game and is not in the score.",
  ],
  ["Multi-source agreement", "One source is collected, so nothing is corroborated."],
  ["Creator supply gap", "No creator-supply data is collected."],
  ["Final opportunity score", "Not defined yet. The current score is an attention measure only."],
] as const;

function List({ items, marker, tone }: { items: readonly (readonly [string, string])[]; marker: string; tone: string }) {
  return (
    <ul className="mt-3 space-y-2.5">
      {items.map(([name, detail]) => (
        <li key={name} className="flex gap-2.5 text-sm">
          <span aria-hidden="true" className={`mt-0.5 font-mono text-xs ${tone}`}>
            {marker}
          </span>
          <span>
            <span className="font-medium text-ink">{name}</span>
            <span className="block text-muted">{detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function CapabilityPanel() {
  return (
    <div className="panel grid gap-x-10 gap-y-6 p-5 md:grid-cols-2">
      <div>
        <h3 className="eyebrow text-up">Available now</h3>
        <List items={AVAILABLE} marker="+" tone="text-up" />
      </div>
      <div>
        <h3 className="eyebrow">Not yet part of the current score</h3>
        <List items={NOT_YET} marker="–" tone="text-faint" />
      </div>
    </div>
  );
}
