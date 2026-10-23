"use client";

type Theme = "light" | "dark";
// The label never names the current theme: the system setting can change under it.
const KEY = "cover-next:theme";

function current(): Theme {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeToggle() {
  function flip() {
    const next: Theme = current() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Private mode or blocked storage: the choice lasts for this page only.
    }
  }

  return (
    <button type="button" className="icon-button" onClick={flip} aria-label="Switch colour theme">
      <span aria-hidden="true">◐</span>
    </button>
  );
}
