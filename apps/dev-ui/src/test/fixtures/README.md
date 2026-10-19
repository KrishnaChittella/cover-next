# Test fixture: one real replay bundle

`bundle/` is an unmodified copy of what the offline replay pipeline published:

```
uv run metaspike run --replay tests/fixtures/wikimedia --as-of 2026-10-03
cd apps/dev-ui && npm run data:sync && cp -R public/data src/test/fixtures/bundle
```

Nothing in it was written or edited by hand. Tests that need a broken or unusual
artifact start from this bundle and change it in memory, in the test.

# Test fixture: the integrated recorded-sources report

`opportunities/integrated.json` is an unmodified copy of the integrated report:

```
cd apps/dev-ui && npm run opportunities:integrated
cp ../../.metaspike/private/opportunities/integrated.json src/test/fixtures/opportunities/
```

It reads the pinned catalogue and pageview recordings and the one genuine recorded Steam
response (app 730, 2026-10-06 01:25:26 UTC) at 2026-10-06 01:30 UTC. The Steam sample is
of unverified origin. The generator is deterministic: the same inputs give the same bytes.
