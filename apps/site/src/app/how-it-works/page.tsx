import type { Metadata } from "next";

export const metadata: Metadata = { title: "How it works" };

export default function HowItWorks() {
  return (
    <article className="prose">
      <h1>How it works</h1>
      <p>
        Once a day Cover Next reads the Wikipedia views of every video game with an English
        Wikipedia article, about 30,000 of them, listed by Wikidata. It then picks out the games
        people are suddenly curious about and checks what is being watched on YouTube for them.
        Games are matched across sources by fixed identifiers from Wikidata, never by title.
      </p>

      <h2>How a game makes the list</h2>
      <p>
        Each game is compared with itself, not with bigger games. Its average views over the last
        3 days are set against its normal level: the middle value of the 28 days before. A small
        indie going from 300 to 3,000 views a day ranks above a blockbuster holding steady at 8,000.
        Games need at least 300 views a day to count, so tiny blips are ignored. A game with too
        little history to have a normal level yet is shown as <strong>New</strong>.
      </p>

      <h2>Studio stories</h2>
      <p>
        When three or more rising games share a developer or publisher on Wikidata, they are shown
        as one story instead of filling the list. The strongest game stays in the list, and the
        rest are tucked inside the story.
      </p>

      <h2>What each number means</h2>
      <table>
        <thead>
          <tr>
            <th>Reading</th>
            <th>Source</th>
            <th>What it tells you</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>vs normal (×)</td>
            <td>Wikimedia page views</td>
            <td>
              How many times its usual daily views the game is getting lately. Rising at ×1.25 or
              more, cooling at ×0.8 or less.
            </td>
          </tr>
          <tr>
            <td>Wiki 7d</td>
            <td>Wikimedia page views</td>
            <td>Wikipedia views over the last 7 days. Missing if any of those days was not measured.</td>
          </tr>
          <tr>
            <td>Top YouTube videos this week</td>
            <td>YouTube Data API</td>
            <td>
              The most-watched of the gaming videos YouTube ranks most relevant for the game among
              those published in the last 7 days, each with its own view count. YouTube is searched
              for the top risers and pinned games each day. We never add these numbers up or average
              them: YouTube&apos;s rules do not allow figures combined across channels.
            </td>
          </tr>
          <tr>
            <td>Steam playing</td>
            <td>Steam Web API (Valve)</td>
            <td>
              How many people were playing the game on Steam at our daily reading, around 13:30 UTC.
              One reading a day, so it shows the trend across days, not the daily peak. After two
              weeks of readings it is also compared with the game&apos;s own normal. Games not on
              Steam say so.
            </td>
          </tr>
          <tr>
            <td>Twitch watching</td>
            <td>Twitch API</td>
            <td>
              Live viewers and the number of live streams for the game at our daily reading, summed
              over its top 300 streams. A game with more streams than that shows &quot;at least&quot;.
              Only these two totals are kept, nothing about any stream or person. Games are matched
              to Twitch by IGDB id, never by name. See{" "}
              <a href="https://discuss.dev.twitch.com/t/can-i-store-aggregated-twitch-game-stats-for-longer-than-24-hours/65174">
                this Twitch developer forum answer
              </a>{" "}
              on keeping such totals.
            </td>
          </tr>
        </tbody>
      </table>

      <h2>What it does not measure</h2>
      <ul>
        <li>
          <strong>PlayStation and Xbox</strong> have no public source for player numbers, and
          reading their stores automatically is against their terms. They are left out.
        </li>
        <li>
          <strong>Your channel.</strong> Nothing here knows your audience, so it cannot predict how
          a video of yours will do.
        </li>
      </ul>

      <h2>When a number is missing</h2>
      <p>
        A missing reading says &ldquo;not measured&rdquo;. It is never shown as zero, and a week
        with a missing day is never added up as if it were complete.
      </p>
    </article>
  );
}
