import { humanize } from "@/lib/format";

/**
 * What the number on this screen is and is not. The first sentence is the artifact's own
 * scope note; the second is the standing distinction every page repeats.
 */
export function ScopeNotice({ scopeNote, scoreVersion }: { scopeNote: string; scoreVersion: string }) {
  return (
    <aside aria-label="Scope of the current score" className="panel border-l-2 border-l-signal px-4 py-3">
      <p className="eyebrow text-signal">
        {humanize(scoreVersion.replace(/^metaspike_/, ""))} is not the final opportunity score
      </p>
      <p className="mt-1.5 max-w-4xl text-sm text-ink-soft">{scopeNote}</p>
      <p className="mt-1.5 max-w-4xl text-sm text-muted">
        <code className="font-mono text-ink-soft">{scoreVersion}</code> is an internal engineering score. It ranks
        how unusual and how fast-moving a game&apos;s attention is. It is not the finished MetaSpike
        creator-opportunity model, and a high value here is not yet a recommendation to make content.
      </p>
    </aside>
  );
}
