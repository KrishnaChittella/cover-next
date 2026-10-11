# Synthetic Steam News fixtures

Every file here is **synthetic**: written by hand in the shape
`ISteamNews/GetNewsForApp/v2` was observed to answer in, for offline tests only. No
file is a recording of a Steam response, and every title, id, author, body and URL is
invented. No real response is kept in the repository, because the source's policy does
not clearly permit it (see `docs/event-sources.md`).

`news_<appid>.json` answers the first request for an app;
`news_<appid>_before_<enddate>.json` answers a backfill page. A file holds
`{"status": <http status>, "body": <response body>}`. App 424242 is bound to no
catalogue game; app 252490 answers 503.
