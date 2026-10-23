import type { Metadata } from "next";

export const metadata: Metadata = { title: "Privacy" };

export default function Privacy() {
  return (
    <article className="prose">
      <p className="label">Last updated 24 October 2026</p>
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

      <h2>Steam data</h2>
      <p>
        Cover Next reads one public figure from the Steam Web API: how many people are playing a
        game at the moment of our daily reading. It never reads anything about you or any Steam
        account. We store one number per game per day (the Steam app id, the date and the player
        count) so the site can show a game&apos;s history. That history is kept in GitHub&apos;s
        storage for this project&apos;s build (GitHub, Inc., United States). Steam data is
        provided by Valve as is, without warranty, and Cover Next is not endorsed by or affiliated
        with Valve or Steam.
      </p>

      <h2>Twitch data</h2>
      <p>
        Once a day Cover Next asks the Twitch API which streams are live for each game it follows
        and keeps two totals per game: how many people were watching and how many streams were
        live. No stream title, channel name, user or other personal detail is kept, and nothing
        about you is read. The totals are kept in the same GitHub storage as the Steam history.
        Our reasons are set out in{" "}
        <a href="https://discuss.dev.twitch.com/t/can-i-store-aggregated-twitch-game-stats-for-longer-than-24-hours/65174">
          this Twitch developer forum thread
        </a>
        ; if Twitch asks us to change this, we will. Cover Next is not endorsed by Twitch.
      </p>

      <h2>Other sources</h2>
      <p>
        Wikipedia page view counts come from the Wikimedia Foundation and game identifiers from
        Wikidata; both are public domain (CC0).
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
