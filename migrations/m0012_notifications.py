"""Notification inbox: every alert the app raises, readable from any page."""

VERSION = "0012_notifications"


def upgrade(conn) -> None:
    import notify_inbox
    notify_inbox.ensure_tables(conn)
