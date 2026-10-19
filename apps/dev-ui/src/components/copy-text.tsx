"use client";
// Copies a finished plain-text string. It is handed the text and nothing else: it reads
// no report, imports nothing private and sends nothing anywhere.
import { useState } from "react";

export function CopyText({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  }
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={copy}
        className="rounded border border-line-strong px-2 py-1 text-xs font-medium text-ink hover:border-signal"
      >
        {label}
      </button>
      <span role="status" className="text-xs text-muted">
        {state === "copied" ? "Copied." : state === "failed" ? "Could not copy: select the text below instead." : ""}
      </span>
    </span>
  );
}
