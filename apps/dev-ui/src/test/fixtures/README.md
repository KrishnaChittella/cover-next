# Test fixture: one real replay bundle

`bundle/` is an unmodified copy of what the offline replay pipeline published:

```
uv run metaspike run --replay tests/fixtures/wikimedia --as-of 2026-10-03
cd apps/dev-ui && npm run data:sync && cp -R public/data src/test/fixtures/bundle
```

Nothing in it was written or edited by hand. Tests that need a broken or unusual
artifact start from this bundle and change it in memory, in the test.
