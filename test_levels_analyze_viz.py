"""Verify → Analyze dials/tiles and the stock page's EMA ladder and Fib ruler."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")
os.environ.setdefault("TRADESTAAR_NO_BACKGROUND", "1")

import web_app  # noqa: E402
import app as legacy  # noqa: E402
from test_fundamentals_render import full, render  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent


def test_analyze_has_a_dial_per_section_and_tones_each_check(full):
    html = render(full)
    assert html.count('class="fa-dial ') == len(full["sections"])
    assert "fa_metrics.js" in html
    assert 'class="metric-row is-' in html
    # Fired triggers lead the downgrade list; the outcome is still a word.
    assert "sort(attribute='fired', reverse=True)" in (ROOT / "templates/fundamentals.html").read_text()


STOCK = dict(ticker="GOOG", current_price=268.5, ema_20_daily=289.84, ema_50_daily=310.08, ema_200_daily=334.57,
             pct_from_ema20=-8.9, daily_trend="Bearish", daily_lh_ll=True, fib_high=355.25, fib_low=272.0,
             fib_236=335.6, fib_382=323.45, fib_50=313.62, fib_618=303.8, fib_65=301.14, fib_705=296.56, fib_786=289.82,
             fib_mode="active", fib_direction="bearish", macro_fib_high=323.13, macro_fib_low=262.27,
             macro_fib_50=292.7, macro_fib_618=285.52, h4_fib_high=293.06, h4_fib_low=270.73, h4_fib_50=281.89, h4_fib_618=279.26)


def _levels_html(stock):
    import jinja2
    src = (ROOT / "templates/stock_detail.html").read_text()
    start = src.index('<section class="sd-panel" id="sd-panel-levels"')
    end = src.index("<!-- ===== INSTITUTIONAL SUPPLY & DEMAND ZONES ===== -->")
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.globals["csrf_token"] = lambda: "x"
    return env.from_string(src[start:end] + "</div></section>").render(stock=stock)


def test_ema_ladder_reads_the_stack():
    html = _levels_html(STOCK)
    assert "Full bearish stack" in html
    assert 'id="detail-cur-price"' in html            # live price updates still land
    assert html.index("200 EMA") < html.index("50 EMA") < html.index("20 EMA")  # sorted high → low
    assert "+24.6%" in html and "overhead" in html
    bull = dict(STOCK, current_price=400.0, ema_20_daily=380.0, ema_50_daily=350.0, ema_200_daily=300.0)
    assert "Full bullish stack" in _levels_html(bull)


def test_fib_ruler_data_and_structure_ranges():
    html = _levels_html(STOCK)
    assert 'id="lv-fib-data"' in html and 'id="lv-ruler"' in html
    assert html.count('class="lv-range"') == 2
    assert "Price is below this range" in html          # 4H: 268.50 under 270.73


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_scripts_parse():
    for f in ("static/js/fa_metrics.js", "static/js/levels_viz.js"):
        subprocess.run(["node", "--check", str(ROOT / f)], check=True)
