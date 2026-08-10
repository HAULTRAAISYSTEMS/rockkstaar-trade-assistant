"""Small, framework-independent navigation helpers."""

from urllib.parse import urlsplit


def safe_post_login_path(value: str | None, default: str = "/terminal") -> str:
    """Return a safe local post-login path, using Terminal for home/default.

    Requested in-app pages are preserved, but absolute URLs, protocol-relative
    URLs, backslash variants, and control characters cannot become redirects.
    The bare home route is intentionally normalized to the Terminal so a fresh
    login opens the trading workspace instead of the research dashboard.
    """
    candidate = (value or "").strip()
    if candidate in {"", "/"}:
        return default
    if "\\" in candidate or any(char in candidate for char in ("\r", "\n", "\x00")):
        return default

    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/") or candidate.startswith("//"):
        return default
    return candidate
