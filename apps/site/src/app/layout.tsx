import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Plus_Jakarta_Sans } from "next/font/google";
import Link from "next/link";

import { ThemeToggle } from "@/components/ThemeToggle";

import "./globals.css";

const sans = Plus_Jakarta_Sans({ subsets: ["latin"], variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: { default: "Cover Next", template: "%s · Cover Next" },
  description:
    "Which game is worth a video right now? Public readings from Wikipedia and YouTube, updated daily.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

// Runs before paint so a saved theme never flashes the other one first.
const themeScript = `try{var t=localStorage.getItem("cover-next:theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <header className="top">
          <div className="wrap">
            <Link href="/" className="brand">
              <span className="brand-name">Cover Next</span>
              <span className="label">What to make a video about</span>
            </Link>
            <nav aria-label="Main">
              <Link href="/games/">All games</Link>
              <Link href="/how-it-works/">How it works</Link>
              <Link href="/privacy/">Privacy</Link>
              <ThemeToggle />
            </nav>
          </div>
        </header>
        <main className="wrap main">{children}</main>
        <footer className="foot">
          <div className="wrap">
            <p>
              Page views from <a href="https://wikimedia.org/api/rest_v1/">Wikimedia</a> and game
              identifiers from <a href="https://www.wikidata.org">Wikidata</a> (both CC0). Video
              data from <a href="https://www.youtube.com/">YouTube</a>, via YouTube API Services (
              <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a>).
            </p>
            <p>
              Game metadata from <a href="https://www.igdb.com">IGDB.com</a>.
            </p>
            <p>
              A project by <a href="https://krishnachittella.dev">Krishna Chittella</a>. Source code on{" "}
              <a href="https://github.com/KrishnaChittella/cover-next">GitHub</a>.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
