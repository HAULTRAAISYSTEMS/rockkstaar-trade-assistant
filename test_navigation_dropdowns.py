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
    assert '>Browser Alerts</a>' in template


def test_navigation_script_closes_sibling_and_outside_menus():
    script = (ROOT / "static/js/main.js").read_text(encoding="utf-8")
    assert '".elite-navbar details.nav-menu, .elite-navbar details.nav-system"' in script
    assert "if (other !== menu) other.open = false" in script
    assert 'event.target.closest(".elite-navbar details")' in script
    assert 'event.key !== "Escape"' in script


def test_mobile_research_learn_and_account_open_menu_sheets():
    template = (ROOT / "templates/_elite_navigation.html").read_text(encoding="utf-8")
    for menu_id in ("mobile-research-menu", "mobile-learn-menu", "mobile-account-menu"):
        assert f'aria-controls="{menu_id}"' in template
        assert f'data-mobile-menu="{menu_id}"' in template
        assert f'id="{menu_id}" hidden' in template
    assert template.count("mobile-app-nav__menu-trigger") == 3
    assert "Company Research" in template
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
