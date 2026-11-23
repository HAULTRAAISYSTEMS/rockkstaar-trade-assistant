import re
from pathlib import Path


ROOT = Path(__file__).parent


def test_elite_navigation_allows_dropdown_panels_to_escape_nav_row():
    css = (ROOT / "static/css/elite.css").read_text(encoding="utf-8")
    assert ".elite-navbar .nav-links { overflow: visible; }" in css
    assert "max-width: min(340px, calc(100vw - 24px));" in css


def test_research_learn_and_account_are_native_dropdowns():
    """Desktop bar is icon tiles: Command, Terminal, Discover, Verify, Watchlists,
    Portfolio, then an avatar menu (Account + Learn). M.C.F.F. and Capital live
    inside Portfolio, not on the bar."""
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    for label in ("Terminal", "Discover", "Portfolio"):
        assert '<summary class="nav-link nav-tile' in template
        assert f'<span class="nav-tile__lbl">{label}</span>' in template
    for label in ("Command", "Verify", "Watchlists"):
        assert f'<span class="nav-tile__lbl">{label}</span>' in template
    for gone in ("M.C.F.F.", "Capital"):
        assert f'<span class="nav-tile__lbl">{gone}</span>' not in template
    assert template.count('<details class="nav-menu">') == 3
    assert '<details class="nav-menu nav-avatar">' in template
    assert "nav_tiles(portfolio_groups)" in template and "capital_groups" not in template


def test_portfolio_menu_puts_schwab_first_and_groups_the_rest():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    block = template[template.index("portfolio_groups = ["):template.index("] %}", template.index("portfolio_groups = ["))]
    groups = re.findall(r"\('(Accounts|Invest|Track|Research)',", block)
    assert groups == ["Accounts", "Invest", "Track", "Research"]
    titles = re.findall(r"url_for\([^)]*\),\s*'([^']+)'", block)
    assert titles == ["Schwab Account", "Wealth Overview", "Next Dollar", "Planner", "Cost Basis Lab",
                      "Scoreboard", "Ledger", "M.C.F.F. list"]


def test_account_dropdown_links_to_free_browser_alerts():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    assert "'browser_push.settings'" in template
    # One Control Center macro holds the account tiles; desktop and the
    # phone sheet both render it.
    assert "'Browser Alerts'" in template and template.count("nav_tiles(account_groups)") == 1
    assert "{{ control_center() }}" in template and "{{ control_center(true) }}" in template


def test_control_center_has_quick_settings_admin_console_and_sign_out():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    for s in ('data-ccx-toggle="sound"', 'data-ccx-toggle="auto"', "pref_24h(", "pref_flash(",
              'class="ccx-admin"', "url_for('admin_speed')", "url_for('live_research.admin_page')",
              "if session.get('is_admin') else [] %}", 'class="ccx-out"', "js/control_center.js"):
        assert s in template, s
    js = (ROOT / "static/js/control_center.js").read_text(encoding="utf-8")
    assert "toggleAlertSound" in js and "toggleAutoRefresh" in js and "tradestaar.cc.sound" in js


def test_navigation_script_closes_sibling_and_outside_menus():
    script = (ROOT / "static/js/main.js").read_text(encoding="utf-8")
    assert '".elite-navbar details.nav-menu, .elite-navbar details.nav-system"' in script
    assert "if (other !== menu) other.open = false" in script
    assert 'event.target.closest(".elite-navbar details")' in script
    assert 'event.key !== "Escape"' in script


def test_mobile_research_learn_and_account_open_menu_sheets():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    for menu_id in ("mobile-terminal-menu", "mobile-research-menu", "mobile-wealth-menu", "mobile-learn-menu", "mobile-account-menu"):
        assert f'aria-controls="{menu_id}"' in template
        assert f'data-mobile-menu="{menu_id}"' in template
        assert f'id="{menu_id}" hidden' in template
    # Terminal opens a sheet too (Terminal, Setups Board, Watchlists & Alerts).
    assert template.count("mobile-app-nav__menu-trigger") == 5
    assert template.count("nav_tiles(wealth_groups)") == 1   # phone Capital sheet
    assert "Company Research" in template
    # Research entries are one grouped list rendered in both menus.
    assert "'Seven Checks'" in template and template.count("nav_tiles(research_groups)") == 2
    assert template.count("url_for('fundamentals_page', tab='quarter')") == 1
    assert "Review Queue" in template
    assert "Browser Alerts" in template


def test_mobile_menu_sheets_are_usable_and_mutually_exclusive():
    script = (ROOT / "static/js/main.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/elite.css").read_text(encoding="utf-8")
    assert 'document.querySelectorAll("[data-mobile-menu]")' in script
    assert "closeMobileMenus(willOpen ? panelId : null)" in script
    assert 'event.target.closest(".mobile-app-nav__group")' in script
    assert ".mobile-app-menu[hidden] { display: none !important; }" in css
    assert "bottom: calc(73px + env(safe-area-inset-bottom, 0px));" in css


def test_watchlists_live_under_terminal_on_desktop_and_phone():
    """Watchlists is a top-level tile on desktop and stays in the Terminal
    sheet on the phone."""
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    assert template.count("url_for('watchlists_page')") == 2 and template.count("nav_tiles(terminal_groups)") == 2
    assert "'Watchlists & Alerts'" in template
    # Five phone tabs: Today, then Terminal, Research, Learn, Account sheets.
    assert template.count('class="mobile-app-nav__item') == 6   # Today, Terminal, Research, Wealth, Learn, Account


def test_research_menu_leads_with_market_intel_and_describes_every_tile():
    import re
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    start = template.index("research_groups = [")
    block = template[start:template.index("] %}", start)]
    titles = re.findall(r"\(\s*'\w+',\s*url_for\([^)]*\),\s*'([^']+)',\s*'([^']*)'", block)
    assert titles[0][0] == "Market Intel"
    assert [t for t, _ in titles] == ["Market Intel", "Macro", "Calendar", "Smart Money", "Company Research",
                                      "Seven Checks", "Thesis", "Live Research", "Memory"]
    assert all(desc for _, desc in titles), "every research tile has a description"


def test_every_menu_tile_has_an_icon_and_description(client_html=None):
    """Rendered: every tile in every menu is icon + title + description."""
    import web_app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
    html = c.get("/intel").get_data(as_text=True)
    tiles = re.findall(r'<a href="[^"]+" class="rm-item[^"]*"><span class="rm-ic"><svg.*?</svg></span><span class="rm-txt"><strong>([^<]+)</strong><small>([^<]+)</small>', html, re.S)
    titles = [t for t, _ in tiles]
    for name in ("Terminal", "Setups Board", "Market Intel", "Memory", "Learning Center", "AI Coach",
                 "Journal", "Risk", "Browser Alerts"):
        assert titles.count(name) == 2, name          # desktop dropdown + phone sheet
    assert all(d.strip() for _, d in tiles)
