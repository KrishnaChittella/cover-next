import type { Manifest } from "@/contract";
import { formatTimestamp } from "@/lib/format";

/** Which published run a page was rendered from. */
export function RunNote({ manifest }: { manifest: Manifest }) {
  return (
    <p className="mt-10 border-t border-line pt-4 font-mono text-xs text-faint">
      run {manifest.run_id} · contract {manifest.schema_version} ({manifest.contract_status}) · {manifest.score_version}{" "}
      · generated {formatTimestamp(manifest.generated_at)}
    </p>
  );
}
