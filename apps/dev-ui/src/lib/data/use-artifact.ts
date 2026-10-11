"use client";

import { useEffect, useState } from "react";

import { DataError, type LoadOptions } from "./client";

export type LoadState<T> =
  | { status: "loading" }
  | { status: "ready"; value: T }
  | { status: "error"; error: DataError };

/**
 * Runs a loader once per `key` and reports loading, ready or error. A failure is always
 * a DataError the page can show; nothing is rendered from a partly loaded artifact.
 */
export function useArtifact<T>(key: string, load: (options: LoadOptions) => Promise<T>): LoadState<T> {
  const [state, setState] = useState<{ key: string; result: LoadState<T> }>({
    key,
    result: { status: "loading" },
  });

  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    load({ signal: controller.signal }).then(
      (value) => {
        if (live) setState({ key, result: { status: "ready", value } });
      },
      (cause: unknown) => {
        if (!live || (cause instanceof DOMException && cause.name === "AbortError")) return;
        const error =
          cause instanceof DataError
            ? cause
            : new DataError("invalid", cause instanceof Error ? cause.message : "Unexpected error.");
        setState({ key, result: { status: "error", error } });
      },
    );
    return () => {
      live = false;
      controller.abort();
    };
    // The loader is identified by `key`; callers pass a stable key per artifact.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state.key === key ? state.result : { status: "loading" };
}
