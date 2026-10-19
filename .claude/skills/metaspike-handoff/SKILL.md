---
name: metaspike-handoff
description: Close out a MetaSpike task with a short handoff in the PR description. Use when a change is ready to push, or when the owner asks for a status summary.
disable-model-invocation: true
---

# Handoff

The PR description is the record. Do not rewrite `docs/handoffs/latest.md` for routine
work, and do not write long narrative documents. Target: **30 lines or fewer.**

## Steps

1. Run the checks for what changed (`/metaspike-checks`). Note the exact counts.
2. Commit on a short-lived branch with a conventional message (`fix:`, `feat:`, `docs:`).
3. Push and open the PR with `gh pr create --fill` or the body below. CI runs on the PR.
4. Report to the owner using the template, nothing more.

## Template

```
## What
One or two sentences: what changed and why.

## Changed
- path/one.py: what
- path/two.py: what

## Checks
- ruff, mypy: pass
- pytest <files>: N passed
- full suite: in CI on this PR

## Owner action
- None. | - Merge, then run deploy (plan → apply). | - Approve <specific thing>.

## Not done / next
- One line each, only if real.
```

Rules: no restating what the diff already shows, no repeating earlier handoffs, no lists
of things that did not change. If something is unverified, say so in one line.
