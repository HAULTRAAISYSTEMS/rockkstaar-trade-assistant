import re
from pathlib import Path


ROOT = Path(__file__).parent


def test_elite_navigation_allows_dropdown_panels_to_escape_nav_row():
    css = (ROOT / "static/css/elite.css").read_text(encoding="utf-8")
    assert ".elite-navbar .nav-links { overflow: visible; }" in css
    assert "max-width: min(340px, calc(100vw - 24px));" in css


def test_research_learn_and_account_are_native_dropdowns():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    for label in ("Research", "Learn", "Account"):
        assert f'<summary class="nav-link' in template
        assert f">{label} <span" in template
    assert template.count('<details class="nav-menu">') == 3


def test_account_dropdown_links_to_free_browser_alerts():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    assert "'browser_push.settings'" in template
    assert "'Browser Alerts'" in template and template.count("nav_tiles(account_groups)") == 2


def test_navigation_script_closes_sibling_and_outside_menus():
    script = (ROOT / "static/js/main.js").read_text(encoding="utf-8")
    assert '".elite-navbar details.nav-menu, .elite-navbar details.nav-system"' in script
    assert "if (other !== menu) other.open = false" in script
    assert 'event.target.closest(".elite-navbar details")' in script
    assert 'event.key !== "Escape"' in script


def test_mobile_research_learn_and_account_open_menu_sheets():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    for menu_id in ("mobile-terminal-menu", "mobile-research-menu", "mobile-learn-menu", "mobile-account-menu"):
        assert f'aria-controls="{menu_id}"' in template
        assert f'data-mobile-menu="{menu_id}"' in template
        assert f'id="{menu_id}" hidden' in template
    # Terminal opens a sheet too (Terminal, Setups Board, Watchlists & Alerts).
    assert template.count("mobile-app-nav__menu-trigger") == 4
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
    """Watchlists moved off the top level: the lists feed the Terminal rail,
    the setups board and alerts, so they sit in the Terminal menu."""
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    assert '<details class="nav-menu nav-menu--split">' in template
    assert template.count("url_for('watchlists_page')") == 1 and template.count("nav_tiles(terminal_groups)") == 2
    assert 'class="nav-link {{ \'nav-link-active\' if ep == \'watchlists_page\' }}">Watchlists</a>' not in template
    # Five phone tabs: Today, then Terminal, Research, Learn, Account sheets.
    assert template.count('class="mobile-app-nav__item') == 5


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
