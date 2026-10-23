import type { Metadata } from "next";

export const metadata: Metadata = { title: "How it works" };

export default function HowItWorks() {
  return (
    <article className="prose">
      <h1>How it works</h1>
      <p>
        Once a day Cover Next asks a few public sources about the same list of games and puts
        what they say side by side. A game is matched across sources by fixed identifiers, never
        by its title.
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
            <td>Wikipedia views, last 7 days</td>
            <td>Wikimedia page views</td>
            <td>
              How many people looked the game up. A sudden rise often follows an update, a launch
              or news, and usually comes before a wave of videos.
            </td>
          </tr>
          <tr>
            <td>Rising curiosity / Steady / Cooling</td>
            <td>Wikimedia page views</td>
            <td>
              The last 7 days against the 7 before: rising at +25% or more, cooling at -20% or
              less. If either week has a missing day there is no comparison.
            </td>
          </tr>
          <tr>
            <td>Top YouTube videos this week</td>
            <td>YouTube Data API</td>
            <td>
              The three most-watched of the gaming videos YouTube ranks most relevant for the
              game among those published in the last 7 days, each with its own view count. It
              shows what is working right now. We do not add these numbers up or average them:
              YouTube&apos;s rules do not allow figures combined across channels.
            </td>
          </tr>
        </tbody>
      </table>

      <h2>What it does not measure</h2>
      <ul>
        <li>
          <strong>Steam players and Twitch viewers</strong> are collected privately but not shown
          yet. Twitch only allows its data to be kept for 24 hours without written permission,
          which we have asked for.
        </li>
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
