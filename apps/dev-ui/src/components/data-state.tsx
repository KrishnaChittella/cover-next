"use client";

// What a page shows while its artifact loads and when it cannot be used. A broken or
// missing artifact is stated plainly; nothing is rendered from it.
import type { ReactNode } from "react";

import { DEFAULT_DATA_BASE, dataBaseUrl } from "@/lib/data/config";
import type { DataError } from "@/lib/data/client";
import type { LoadState } from "@/lib/data/use-artifact";

const HEADINGS: Record<DataError["kind"], string> = {
  unavailable: "The artifacts could not be loaded",
  invalid: "An artifact does not match the contract",
  inconsistent: "The artifacts do not belong together",
  not_found: "Not in the current run",
};

export function LoadingBlock({ label, rows = 6 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="panel p-5">
      <span className="sr-only">Loading {label}…</span>
      <div aria-hidden="true" className="space-y-3">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="h-4 animate-pulse rounded-sm bg-raised" style={{ width: `${92 - (index % 4) * 11}%` }} />
        ))}
      </div>
    </div>
  );
}

export function ErrorBlock({ error }: { error: DataError }) {
  const base = dataBaseUrl();
  return (
    <div role="alert" className="panel border-bad/50 p-5">
      <p className="eyebrow text-bad">Nothing is shown from this data</p>
      <h2 className="mt-2 text-lg font-semibold text-ink">{HEADINGS[error.kind]}</h2>
      <p className="mt-2 max-w-3xl text-sm text-ink-soft">{error.message}</p>
      {error.details.length > 0 ? (
        <ul className="mt-3 max-w-3xl space-y-1 font-mono text-xs text-ink-soft">
          {error.details.slice(0, 8).map((violation, index) => (
            <li key={index}>
              <span className="text-signal">{violation.path}</span> {violation.message}
            </li>
          ))}
          {error.details.length > 8 ? <li className="text-muted">and {error.details.length - 8} more</li> : null}
        </ul>
      ) : null}
      <div className="mt-4 max-w-3xl text-sm text-muted">
        {error.kind === "unavailable" ? (
          <p>
            Data base URL: <code className="font-mono text-ink-soft">{base}</code>.{" "}
            {base === DEFAULT_DATA_BASE ? (
              <>
                Copy a validated bundle into place with{" "}
                <code className="font-mono text-ink-soft">npm run data:sync</code>, or build one offline with{" "}
                <code className="font-mono text-ink-soft">npm run data:replay</code>.
              </>
            ) : (
              <>Check that it serves a manifest.json.</>
            )}
          </p>
        ) : error.kind === "invalid" || error.kind === "inconsistent" ? (
          <p>
            The schema checked in under <code className="font-mono text-ink-soft">schemas/</code> is the authority.
            If the contract changed, run <code className="font-mono text-ink-soft">npm run contract:generate</code>{" "}
            and sync the data again.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Renders `children` only for an artifact that loaded and validated. */
export function DataState<T>({
  state,
  label,
  rows,
  children,
}: {
  state: LoadState<T>;
  label: string;
  rows?: number;
  children: (value: T) => ReactNode;
}) {
  if (state.status === "loading") return <LoadingBlock label={label} rows={rows} />;
  if (state.status === "error") return <ErrorBlock error={state.error} />;
  return <>{children(state.value)}</>;
}
