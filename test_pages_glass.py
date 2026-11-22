"""Insider watchlist, Thesis and Schwab Account glass upgrade."""
import re
from pathlib import Path

import jinja2

ROOT = Path(__file__).resolve().parent
TPL = ROOT / "templates"


def _sgn():
    src = (TPL / "schwab_account.html").read_text()
    macro = re.search(r"{%-?\s*macro sgn\(.*?{%-?\s*endmacro\s*-?%}", src, re.S).group(0)
    env = jinja2.Environment()
    return env.from_string(macro + "{{ sgn(v) }}")


def test_signed_money_never_prints_dollar_minus():
    t = _sgn()
    assert t.render(v=-1084.79).strip() == "−$1,084.79"
    assert t.render(v=12.5).strip() == "+$12.50"
    assert t.render(v=-0.001).strip() == "$0.00"
    assert t.render(v=0).strip() == "$0.00"


def test_schwab_template_has_no_raw_signed_formats():
    src = (TPL / "schwab_account.html").read_text()
    assert "'+' if account_data.daily_pnl" not in src
    assert "sa-pos-list" in src
    assert "wa-card" in src


def test_smart_money_watchlist_cards_and_filter():
    src = (TPL / "smart_money.html").read_text()
    for cls in ("smw-strip", "smw-filter", "smw-grid", "smw-card", "smw-bar"):
        assert cls in src
    js = (ROOT / "static/js/smart_money.js").read_text()
    assert "data-smw" in js or "smw" in js
    assert "watch_totals" in (ROOT / "app.py").read_text()


def test_thesis_price_map_and_phone_column():
    src = (TPL / "thesis.html").read_text()
    assert "vz-map" in src and "vz-map__now" in src
    css = (ROOT / "static/css/thesis.css").read_text()
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert re.search(r"\.th-shell \.wa-card\s*{\s*border-radius:\s*18px", css)
