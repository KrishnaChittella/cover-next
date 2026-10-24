"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ThemeToggle } from "@/components/ThemeToggle";

const NAV = [
  { href: "/", label: "Trending" },
  { href: "/games/", label: "All games" },
  { href: "/how-it-works/", label: "How it works" },
  { href: "/privacy/", label: "Privacy" },
];

/** Brand, a game search (press / or Ctrl+K), and pill navigation that marks the current page. */
export function SiteHeader() {
  const path = usePathname();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      if ((e.key === "/" && !typing) || (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href.slice(0, -1)));
  return (
    <header className="top">
      <div className="wrap">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="18" height="18">
              <path
                fill="currentColor"
                d="M12 2c1 3.5 4.5 5.2 4.5 9.6A4.5 4.5 0 0 1 12 16a4.5 4.5 0 0 1-4.5-4.4c0-1.6.6-2.9 1.6-3.9.2 1.5 1 2.4 2 2.7C10.4 7.7 11 4.6 12 2Zm-6 12.4A6 6 0 0 0 12 22a6 6 0 0 0 6-7.6A6.5 6.5 0 0 1 12 18a6.5 6.5 0 0 1-6-3.6Z"
              />
            </svg>
          </span>
          <span className="brand-text">
            <span className="brand-name">
              Cover Next <span className="beta">Beta</span>
            </span>
            <span className="label">What to make a video about</span>
          </span>
        </Link>
        <form
          className="search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            // A full load, so /games/ reads the new query even when it is already open.
            window.location.href = `/games/?q=${encodeURIComponent(query.trim())}`;
          }}
        >
          <input
            ref={input}
            type="search"
            placeholder="Search games"
            aria-label="Search games"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <kbd aria-hidden="true">/</kbd>
        </form>
        <nav aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="pill-link" aria-current={active(n.href) ? "page" : undefined}>
              {n.label}
            </Link>
          ))}
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
