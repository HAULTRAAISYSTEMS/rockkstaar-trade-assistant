# Telegram notification policy

Telegram uses the existing bot token and chat ID. The app's dashboard feeds
are unchanged.

- Weekly calendar: Sunday at or after 6 PM America/New_York, on the worker's
  30-minute sweep. Weekday catch-up after 7 AM if the service missed Sunday.
  Includes major economic events and watchlist earnings, dividends and splits.
  Up to six items per section; full details remain in the app.
- Setups: regular market hours, up to three per Eastern calendar day. Watchlist
  candidates need scanner score 7+; other candidates need score 10. Both must
  be above VWAP and near the intraday high. One alert per ticker per day.
  Messages show the intraday-high trigger reference, VWAP invalidation reference,
  reason and observation time. This covers the configured scanner universe plus
  watchlist names, not every listed security.
- Important data: one grouped reminder on high-impact economic-event days;
  one compact news digest per day with up to three qualifying headlines.
  Extreme conditions (VIX 35+ or a 15+ basis-point daily Treasury move)
  receive at most one additional grouped alert per day.
  Critical news must concern watchlist names or SPY/QQQ; HIGH news must concern
  watchlist names. Routine dividends/splits/earnings do not send individually.
  This version reports scheduled macro events, not release results. Existing
  calendar caching/provider limitations make timely result alerts unreliable.

`TELEGRAM_USER_ID` optionally restricts watchlist membership to an account.
Without it, the deployment-wide bot uses all existing watchlist memberships,
consistent with the pre-existing shared-bot model.

Migration `0008_telegram_policy` is registered in the startup migration runner.
It stores per-recipient delivery identities and daily counts in the existing
application database, with a transaction lock to serialize workers. Failed
requests remain retryable. A process crash between Telegram accepting a message
and the database commit can cause a repeat; exactly-once external delivery is
not guaranteed. Keep the application database persistent across deployments.

Offline tests: `python -m unittest test_telegram_policy -v`.
