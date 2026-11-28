import re
from pathlib import Path

from flask import render_template, session
import pytest

ROOT = Path(__file__).parent
DESTINATIONS = ["Command", "Terminal", "Research", "Watchlists", "Portfolio"]


@pytest.fixture
def nav():
    from web_app import app

    def render(path, **context):
        with app.test_request_context(path):
            session.update(user_id=1, username="test")
            return render_template("_elite_navigation.html", **context)

    return render


def test_elite_navigation_allows_dropdown_panels_to_escape_nav_row():
    css = (ROOT / "static/css/elite.css").read_text(encoding="utf-8")
    assert ".elite-navbar .nav-links { overflow: visible; }" in css
    assert "max-width: min(340px, calc(100vw - 24px));" in css


@pytest.mark.parametrize(("path", "active"), [
    ("/opportunity", "Command"),
    ("/terminal", "Terminal"),
    ("/trade-gate?ticker=NVDA", "Terminal"),
    ("/intel", "Research"),
    ("/fundamentals?ticker=NVDA&tab=quarter", "Research"),
    ("/thesis?ticker=NVDA", "Research"),
    ("/watchlists", "Watchlists"),
    ("/wealth/research", "Watchlists"),
    ("/wealth/performance?p=7", "Portfolio"),
])
def test_desktop_and_phone_share_one_active_destination(nav, path, active):
    html = nav(path)
    desktop = html.split('aria-label="Desktop destinations">', 1)[1].split('<div class="nav-actions">', 1)[0]
    phone = html.split('aria-label="Primary mobile navigation">', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'class="nav-tile__lbl">([^<]+)', desktop) == DESTINATIONS
    assert re.findall(r'class="mobile-app-nav__label">([^<]+)', phone) == DESTINATIONS
    assert desktop.count('aria-current=') == phone.count('aria-current=') == 1
    assert re.search(r'aria-current="(?:page|true)">\s*<span class="nav-tile__ic">.*?</span><span class="nav-tile__lbl">' + active, desktop, re.S)
    assert re.search(r'aria-current="(?:page|true)">\s*<span class="mobile-app-nav__icon">.*?</span><span class="mobile-app-nav__label">' + active, phone, re.S)
    assert re.findall(r'<a href="([^"]+)"', phone) == ["/opportunity", "/terminal", "/intel", "/watchlists", "/wealth"]


def test_portfolio_menu_uses_account_context_and_one_performance_link(nav):
    html = nav("/wealth/performance?p=7", current={"id": 7})
    portfolio = html.split('<span class="rm-sec">Track</span>', 1)[1].split('<div class="nav-actions">', 1)[0]
    titles = re.findall(r'<strong>([^<]+)</strong>', portfolio)
    assert titles == ["Overview", "Performance", "Transactions", "Contributions", "Next Dollar", "Cost Basis Lab", "Schwab Accounts"]
    for path in ("/wealth?p=7", "/wealth/performance?p=7", "/wealth/ledger?p=7"):
        assert f'href="{path}"' in portfolio


def test_only_current_workspace_tools_appear_above_phone_content(nav):
    terminal = nav("/terminal?ticker=NVDA").split('class="nav-phone-tools"')[1]
    assert "<summary>Terminal tools</summary>" in terminal
    assert 'href="/trade-gate?ticker=NVDA"' in terminal
    assert "<summary>Research tools</summary>" not in terminal
    research = nav("/intel").split('class="nav-phone-tools"')[1]
    assert "<summary>Research tools</summary>" in research
    assert "<summary>Terminal tools</summary>" not in research
    for markup in (terminal, research):
        assert '<strong>Learning Center</strong>' in markup
        assert '<strong>Browser Alerts</strong>' in markup
        assert '<strong>Watchlists &amp; Alerts</strong>' not in markup


def test_control_center_has_quick_settings_admin_console_and_sign_out():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    for snippet in ('data-ccx-toggle="sound"', 'data-ccx-toggle="auto"', "pref_24h(", "pref_flash(",
                    'class="ccx-admin"', "url_for('admin_speed')", "url_for('live_research.admin_page')",
                    "if session.get('is_admin') else [] %}", 'class="ccx-out"', "js/control_center.js"):
        assert snippet in template
    js = (ROOT / "static/js/control_center.js").read_text(encoding="utf-8")
    assert all(name in js for name in ("toggleAlertSound", "toggleAutoRefresh", "tradestaar.cc.sound"))


def test_navigation_script_closes_sibling_and_outside_menus():
    script = (ROOT / "static/js/main.js").read_text(encoding="utf-8")
    assert ".elite-navbar details.nav-menu, .elite-navbar details.nav-system, .nav-phone-tools details" in script
    assert "if (other !== menu) other.open = false" in script
    assert 'event.target.closest(".elite-navbar details, .nav-phone-tools details")' in script
    assert 'event.key !== "Escape"' in script
