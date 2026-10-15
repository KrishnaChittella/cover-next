# Supervised Wikimedia Recording: Owner Runbook

U6.7. Code: `src/metaspike/pageview_recorder.py`. Command: `metaspike record-pageviews`.
Tests: `tests/test_pageview_recorder.py`, which uses mock transports only.

| Part | Status |
|---|---|
| Plan, record, inspect, refusals and the replay format | **IMPLEMENTED AND OFFLINE TESTED** |
| A live run against Wikimedia | **NOT EXECUTED — LIVE OWNER APPROVAL REQUIRED** |

No request has ever been sent to Wikimedia by this command.

## Before a live session

All of these must hold:

1. **The C2-F acceptance review has been performed and has passed.** This is a separate
   task with explicit owner approval, and it has not happened yet. The recorder does not
   read, touch or depend on production.
2. **The owner has explicitly authorised this live recording session.**
3. **U6.7 has been reviewed and approved.**
4. **`METASPIKE_CONTACT` holds the contact for the User-Agent**, as Wikimedia's
   User-Agent policy asks. If it is unset, the default is `https://krishnachittella.dev`.
   It is sent in the User-Agent header and is never written to disk.

No credential is involved. Wikimedia is keyless and its data is CC0. No cloud credential
is read.

## 1. Preview the scope (sends nothing)

```
uv run metaspike record-pageviews --all-bootstrap \
  --out .metaspike/recordings/wikimedia-YYYYMMDD
```

The preview prints:

- the plan id;
- the policy's review date and digest;
- the latest day Wikimedia had published (the day before yesterday, under its 24-hour
  lag);
- the 90-day window ending on that day;
- every URL that would be requested: 14, one per bootstrap game with an article (FiveM
  has none).

To record a subset, use `--game SLUG` (repeatable, at most 15). To end the window earlier,
use `--through YYYY-MM-DD`; a day not yet published is refused.

The command refuses in these cases:

- a destination that already exists;
- a destination whose `.incomplete` twin exists;
- a destination under `artifacts/`, `public/`, `fixtures/`, `apps/`, `infra/` or
  `.github/`.

## 2. Record (owner only, once authorised)

```
uv run metaspike record-pageviews --all-bootstrap \
  --out .metaspike/recordings/wikimedia-YYYYMMDD --live --confirm PLAN_ID
```

`PLAN_ID` is the id the preview printed. If the scope, the window, the destination or the
policy differs from what was previewed, the id differs and nothing is sent. This includes
the day changing at midnight UTC.

**Limits**

- At most 15 requests: one per target, sent one at a time.
- The policy's documented limit is 200 requests a minute.
- No retry. The attempt budget equals the number of planned requests.

**Output**

```
.metaspike/recordings/wikimedia-YYYYMMDD/
  <Article>.json              each 200 response body, byte for byte (the replay format)
  acquisition/manifest.json   the plan, and every request's outcome, failures included
  acquisition/records.jsonl   one acquisition_record_v1 per kept body
```

During the run the output is written to `wikimedia-YYYYMMDD.incomplete/` and renamed when
the run ends.

**Success** looks like `WIKIMEDIA RECORDING: COMPLETE`, with 14 targets recorded (or
`no_data` where Wikimedia has no data), and a digest marked *NOT pinned*.

**STOP conditions.** The recorder stops itself, writes the manifest as `PARTIAL` or
`FAILED`, and keeps only what succeeded, in any of these cases:

- HTTP 429 (rate limited);
- HTTP 401 or 403, or any 5xx;
- a body that does not parse;
- a connection failure or timeout;
- a request differing from the plan.

A 404 means "no data for this article". It is recorded as such, and the run continues.
Nothing is invented for any failure.

**Stopping by hand.** Ctrl-C at any point. A leftover `.incomplete` directory is never
mistaken for a recording. Delete it.

## 3. Inspect and check

```
uv run metaspike record-pageviews --inspect .metaspike/recordings/wikimedia-YYYYMMDD
```

It checks:

- every body against its acquisition record (SHA-256 and length);
- that every file has a record, and every record a file;
- that the files are exactly those the manifest lists;
- that the bodies' digest equals the one written at acquisition;
- that the source policy is unchanged since acquisition;
- that every body replays through the existing `replay_client` as a series.

It also prints the window, the acquisition times, the digest, whether it is pinned, and
the standing, which is **unverified** until pinned. Any problem gives exit code 4.

A reviewer also reads `acquisition/manifest.json` directly. It holds, per request:

- the method and path, with no query;
- the requested and received times;
- the status;
- a short list of kept response headers (content type, caching, date, etag,
  retry-after).

Request headers, cookies and any other response header are never written.

The digest is `recording_digest(directory)`: SHA-256 over each top-level `.json` body's
name and SHA-256, in sorted order. It is the same function that produced the pinned
digest of `tests/fixtures/wikimedia`, and it covers the bodies only. The acquisition
records are checked against those bodies, not included in the digest.

## 4. Pin (owner approval required)

A recording becomes verified only through this step. There is no flag for it.

1. The owner reviews the inspection output and the manifest.
2. The owner approves pinning that exact digest.
3. In a reviewed commit, the digest is added to `PINNED_RECORDINGS` in
   `src/metaspike/evidence/provenance.py`, with a one-line description: source, articles,
   window and acquisition date.
4. The digest, and nothing else about the recording, is reviewed in. If the recording
   changes by one byte, it is no longer verified.

## 5. Private evidence and the private UI

```
uv run metaspike evidence --as-of <a moment after the recording's received times> \
  --wikimedia-replay .metaspike/recordings/wikimedia-YYYYMMDD \
  --recorded-through <window end> --opportunities
```

To refresh the private UI report, run the same command with `--json --out
.metaspike/private/opportunities/verified.json`, then open `/opportunities` with
`METASPIKE_PRIVATE_OPPORTUNITIES=1 npm run dev` in `apps/dev-ui`.

The `--as-of` moment must not precede the recording's received times. A recording made
today is not evidence of what was visible at an earlier moment. Its values are used under
the existing backtest assumptions, and a revision the source made earlier cannot be seen
in it.

## 6. Discard or roll back

Delete the recording directory, and any `.incomplete` twin. Nothing else is changed by a
recording: no production resource, no artifact, no pinned digest. If a digest was pinned
and must be withdrawn, revert that commit.

## What this does not do

- Run unattended or on a schedule.
- Discover games.
- Touch production storage or the C2-F soak.
- Publish anything.
- Pin anything.
- Mark anything verified.
- Read any credential.

Steam, YouTube, Reddit and event sources are untouched and stay on the roadmap. See
[source-readiness-and-real-data-activation.md](source-readiness-and-real-data-activation.md).
