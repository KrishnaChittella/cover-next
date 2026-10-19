import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { SiteHeader } from "@/components/site-header";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MetaSpike (internal development)", template: "%s · MetaSpike (internal)" },
  description: "Private developer interface over MetaSpike's internal artifacts. Not a public product.",
  // Private and local. Nothing here is to be indexed, even by accident.
  robots: { index: false, follow: false, nocache: true },
};

export const viewport: Viewport = { themeColor: "#0b0c0f", colorScheme: "dark" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-raised focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        <SiteHeader />
        <main id="content" className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          {children}
        </main>
        <footer className="border-t border-line">
          <p className="mx-auto max-w-6xl px-4 py-5 text-xs text-muted sm:px-6">
            Private developer interface. It shows computed values from MetaSpike&apos;s internal artifact contract
            and computes no score itself. The contract is internal and non-binding; nothing here is a public
            release.
          </p>
        </footer>
      </body>
    </html>
  );
}
