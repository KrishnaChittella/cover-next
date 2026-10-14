"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/games", label: "Games" },
  { href: "/opportunities", label: "Opportunities" },
  { href: "/radar", label: "Radar" },
  { href: "/validation", label: "Validation" },
  { href: "/sources", label: "Sources" },
  { href: "/methodology", label: "Methodology" },
] as const;

function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname() ?? "/";
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-2 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-base font-semibold tracking-tight text-ink">
            MetaSpike
          </Link>
          <span className="rounded border border-signal/50 px-1.5 py-0.5 text-[0.6875rem] font-semibold tracking-wider text-signal">
            INTERNAL DEVELOPMENT
          </span>
        </div>
        <nav aria-label="Primary" className="-mx-2 flex flex-wrap items-center gap-x-1">
          {LINKS.map((link) => {
            const current = isCurrent(pathname, link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={current ? "page" : undefined}
                className={`rounded px-2 py-1.5 text-sm transition-colors ${
                  current
                    ? "font-medium text-ink underline decoration-signal decoration-2 underline-offset-8"
                    : "text-muted hover:text-ink"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
