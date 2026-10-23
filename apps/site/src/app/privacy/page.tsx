import type { Metadata } from "next";

export const metadata: Metadata = { title: "Privacy" };

export default function Privacy() {
  return (
    <article className="prose">
      <p className="label">Last updated 23 October 2026</p>
      <h1>Privacy</h1>
      <p>
        Cover Next has no accounts, no sign-in, no analytics, no ads and no cookies. It is a set
        of static pages. Nothing you do on it is sent to us.
      </p>

      <h2>What stays in your browser</h2>
      <p>
        Two things are saved in your browser&apos;s local storage, and only there: the games you
        star (your watchlist) and your light or dark theme choice. We never receive them. Clear
        your browser&apos;s site data to remove them.
      </p>

      <h2>Hosting</h2>
      <p>
        The pages are served by GitHub Pages. GitHub may record technical details of each visit,
        such as your IP address, under the{" "}
        <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">
          GitHub Privacy Statement
        </a>
        . We do not receive or use those records. Fonts are served from the same place, not from
        Google.
      </p>

      <h2>YouTube API Services</h2>
      <p>
        Cover Next uses YouTube API Services to read public information about recent videos:
        titles, publication times and view counts. It does not use your YouTube account and
        collects nothing about you through YouTube. By looking at the YouTube figures here you are
        also covered by the <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a>{" "}
        and the <a href="https://policies.google.com/privacy">Google Privacy Policy</a>. YouTube
        data is refreshed daily and anything older than 30 days is deleted.
      </p>

      <h2>Other sources</h2>
      <p>
        Wikipedia page view counts come from the Wikimedia Foundation and game identifiers from
        Wikidata; both are public domain (CC0). Steam and Twitch data are collected privately for testing and are not shown on this
        site.
      </p>

      <h2>Contact</h2>
      <p>
        Questions or requests:{" "}
        <a href="https://github.com/KrishnaChittella/cover-next/issues">open an issue on GitHub</a>
        , or reach Krishna Chittella through <a href="https://krishnachittella.dev">krishnachittella.dev</a>.
      </p>
    </article>
  );
}
