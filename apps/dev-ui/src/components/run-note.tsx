import type { Manifest } from "@/contract";
import { formatTimestamp, humanize } from "@/lib/format";

/** Which published run a page was rendered from, and whether it may leave this machine. */
export function RunNote({ manifest }: { manifest: Manifest }) {
  const restricted = manifest.restricted_sources;
  return (
    <>
      {manifest.distribution === "private_development_only" ? (
        <aside
          data-testid="private-bundle"
          aria-label="Private development data"
          className="panel mt-10 border-l-2 border-l-warn px-5 py-4"
        >
          <p className="eyebrow text-warn">Private development only: not for publication</p>
          <p className="mt-1.5 max-w-4xl text-sm text-ink-soft">
            This bundle holds event data derived from{" "}
            {restricted.map((source) => humanize(source.source)).join(", ")}, which is cleared for private
            development and not for public launch or commercial use. Nothing here may be published.
          </p>
          <ul className="mt-2 space-y-1 text-xs text-muted">
            {restricted.map((source) => (
              <li key={source.source} className="max-w-4xl">
                <span className="font-mono text-ink-soft">{source.source}</span>: {source.reason}
              </li>
            ))}
          </ul>
        </aside>
      ) : null}
      <p className="mt-10 border-t border-line pt-4 font-mono text-xs text-faint">
        run {manifest.run_id} · contract {manifest.schema_version} ({manifest.contract_status}) ·{" "}
        {manifest.score_version} · generated {formatTimestamp(manifest.generated_at)}
      </p>
    </>
  );
}
