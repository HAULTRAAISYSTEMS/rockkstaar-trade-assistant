"""Market-wide SEC Form 4 feed: open-market buys and sells, plus seen filings.

Filled by the 5-minute cron (insider_feed.sync) from SEC's live Form 4 feed
and its daily indexes, so the Smart Money page reads stored rows instead of
calling the SEC on every page load, and covers every ticker, not ten.
"""

VERSION = "0011_insider_feed"


def upgrade(conn) -> None:
    import insider_feed
    insider_feed.ensure_tables(conn)
