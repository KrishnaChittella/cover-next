import Link from "next/link";

import { Sparkline } from "@/components/Sparkline";
import {
  byRise,
  folded,
  formatCount,
  formatDay,
  formatRatio,
  formatShort,
  formatUpdated,
  trendOf,
  type Game,
  type SiteData,
} from "@/lib/site";

const STATUS_TEXT = { ok: "Live", missing: "No reading", pending: "Waiting" } as const;

export function LeftRail({ data }: { data: SiteData }) {
  const listed = data.games.filter((g) => !folded(g));
  const movers = listed.filter((g) => trendOf(g) === "rising").sort(byRise).slice(0, 5);
  const top = movers[0]?.rise?.ratio ?? 1;
  const radar = [
    { href: "/games/", label: "All tracked games", n: data.games.length, tone: "primary" },
    { href: "/games/?f=rising", label: "Rising", n: data.games.filter((g) => trendOf(g) === "rising").length, tone: "ok" },
    { href: "/games/?f=new", label: "Newly noticed", n: data.games.filter((g) => trendOf(g) === "new").length, tone: "tertiary" },
    { href: "/games/?f=pinned", label: "Pinned", n: data.games.filter((g) => g.pinned).length, tone: "secondary" },
    { href: "/games/?f=watchlist", label: "Your watchlist", n: null, tone: "muted" },
  ];
  const s = data.sources;
  const sources = [
    { name: "Wikipedia", state: s.wikipedia.state, note: s.wikipedia.games ? `${formatCount(s.wikipedia.games)} games read` : "" },
    { name: "YouTube", state: s.youtube.state, note: s.youtube.games ? `${s.youtube.games} games searched` : "" },
    { name: "Steam", state: s.steam.state, note: s.steam.games ? `${s.steam.games} games read` : "" },
    { name: "Twitch", state: s.twitch.state, note: s.twitch.games ? `${s.twitch.games} games read` : "" },
  ];
  return (
    <aside className="rail rail-left" aria-label="Filters and status">
      <section className="panel">
        <h2 className="panel-title caps">Curiosity radar</h2>
        <ul className="radar">
          {radar.map((r, i) => (
            <li key={r.href}>
              <Link href={r.href} aria-current={i === 0 ? "true" : undefined}>
                <span className={`dot tone-${r.tone}`} aria-hidden="true" />
                <span className="radar-label">{r.label}</span>
                {r.n !== null && <span className={`badge tone-${r.tone}`}>{r.n}</span>}
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title caps">Velocity movers</h2>
          <span className="label">Wiki ×</span>
        </div>
        <ol className="movers">
          {movers.map((g, i) => (
            <li key={g.slug}>
              <div className="mover-line">
                <span className="mono rank-sm">{String(i + 1).padStart(2, "0")}</span>
                <Link href={`/games/${g.slug}/`}>{g.name}</Link>
                <span className="mono up">{formatRatio(g.rise?.ratio)}</span>
              </div>
              <div className="bar" aria-hidden="true">
                <span style={{ width: `${Math.max(6, ((g.rise?.ratio ?? 0) / top) * 100)}%` }} />
              </div>
              <div className="mover-sub mono">
                <span>{formatShort(g.wikipedia.last_7_days)} views · 7d</span>
                <span>{g.story ? `${g.story.name} story` : `${formatShort(g.rise?.recent)}/day`}</span>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title caps">Source status</h2>
          <span className="label">{formatDay(data.day)}</span>
        </div>
        <ul className="status">
          {sources.map((src) => (
            <li key={src.name}>
              <span>{src.name}</span>
              <span className={`badge state-${src.state}`}>{STATUS_TEXT[src.state]}</span>
              {src.note && <span className="sub">{src.note}</span>}
            </li>
          ))}
        </ul>
        <p className="sub mono">Updated {formatUpdated(data.generated_at)}</p>
      </section>
    </aside>
  );
}

/** Why the spotlight game stands out, from figures only: no adjective without a number. */
function reasons(game: Game): string[] {
  const out: string[] = [];
  if (game.rise?.ratio) out.push(`Wikipedia ${formatRatio(game.rise.ratio)} its normal (${formatCount(game.rise.recent)} a day)`);
  const o = game.opportunity;
  if (o && game.twitch.state === "ok")
    out.push(`${formatCount(Math.round(o.viewers_per_stream))} Twitch viewers per live stream (${formatCount(game.twitch.streams_now)} streams)`);
  if (game.steam.state === "ok") out.push(`${formatCount(game.steam.now)} playing on Steam`);
  return out;
}

export function Spotlight({ game }: { game: Game | undefined }) {
  if (!game) return null;
  const top = game.youtube.state === "ok" ? game.youtube.top_videos[0] : undefined;
  return (
    <section className="panel spotlight">
      <div className="panel-head">
        <h2 className="panel-title caps">Breakout spotlight</h2>
        {game.opportunity && <span className="badge tone-secondary">Top opportunity</span>}
      </div>
      <h3 className="spot-name">
        <Link href={`/games/${game.slug}/`}>{game.name}</Link>
      </h3>
      <ul className="reasons">
        {reasons(game).map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      {game.wikipedia.state === "ok" && <Sparkline days={game.wikipedia.daily} label={`Daily Wikipedia views for ${game.name}`} />}
      {top && (
        <a className="clip" href={`https://www.youtube.com/watch?v=${top.id}`} rel="noopener">
          <span className="label">Most-watched video this week · {formatCount(top.views)} views</span>
          <span className="clip-title">{top.title}</span>
        </a>
      )}
      <p className="sub">Picked by the opportunity reading below; a reading, not a promise.</p>
    </section>
  );
}

export function OpportunityMeter({ data, game }: { data: SiteData; game: Game | undefined }) {
  const ranked = data.games
    .filter((g) => g.opportunity && !folded(g))
    .sort((a, b) => (b.opportunity?.score ?? 0) - (a.opportunity?.score ?? 0));
  const o = game?.opportunity;
  return (
    <section className="panel">
      <div className="panel-head">
        <h2 className="panel-title caps">Opportunity meter</h2>
        {o && game && <span className={`badge sat-${o.saturation}`}>{game.name.split(":")[0]}</span>}
      </div>
      {o && game ? (
        <div className="meter">
          <div className="meter-head">
            <span>Curiosity vs supply</span>
            <span className="mono hot">{o.score} / 100</span>
          </div>
          <div className="meter-bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={o.score} aria-label={`Opportunity ${o.score} of 100`}>
            <span style={{ width: `${o.score}%` }} />
          </div>
          <div className="meter-foot mono">
            <span>
              Curiosity <strong className="up">{formatRatio(game.rise?.ratio)}</strong>
            </span>
            <span>
              Creator saturation <strong className={`sat-text-${o.saturation}`}>{o.saturation}</strong>
            </span>
          </div>
        </div>
      ) : (
        <p className="missing">No game has both a Wikipedia rise and Twitch streams today.</p>
      )}
      {ranked.length > 1 && (
        <ol className="mini opp-list">
          {ranked.slice(0, 5).map((g) => (
            <li key={g.slug}>
              <Link href={`/games/${g.slug}/`}>{g.name}</Link>
              <span className="mono">{g.opportunity?.score}</span>
            </li>
          ))}
        </ol>
      )}
      <p className="sub">
        Curiosity: Wikipedia against each game&apos;s normal. Supply: Twitch viewers per live stream. Both ranked among
        today&apos;s games. <Link href="/how-it-works/#opportunity">How it is worked out</Link>
      </p>
    </section>
  );
}

/** Studio stories: games of one developer or publisher rising together, folded into one group. */
export function GroupedGames({ data }: { data: SiteData }) {
  if (data.stories.length === 0) return null;
  const bySlug = new Map(data.games.map((g) => [g.slug, g]));
  return (
    <section className="panel" aria-label="Grouped games">
      <div className="panel-head">
        <h2 className="panel-title caps">Grouped games</h2>
        <span className="badge tone-muted">{data.stories.length} studios</span>
      </div>
      {data.stories.map((s, i) => {
        const members = s.members.flatMap((m) => bySlug.get(m) ?? []);
        return (
          <details key={s.slug} id={s.slug} className="group" open={i === 0}>
            <summary>
              <span className="group-name">{s.name}</span>
              <span className="label">{s.role === "developer" ? "studio" : "publisher"}</span>
              <span className="badge tone-primary">{members.length}</span>
            </summary>
            <ul className="mini">
              {members.map((g) => (
                <li key={g.slug}>
                  <Link href={`/games/${g.slug}/`}>{g.name}</Link>
                  <span className="mono up">{formatRatio(g.rise?.ratio)}</span>
                </li>
              ))}
            </ul>
          </details>
        );
      })}
      <p className="sub">Three or more rising games from one developer or publisher are grouped here instead of filling the list.</p>
    </section>
  );
}
