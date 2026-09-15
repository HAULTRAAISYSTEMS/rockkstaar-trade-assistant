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


def test_navigation_script_closes_sibling_and_outside_menus():
    script = (ROOT / "static/js/main.js").read_text(encoding="utf-8")
    assert '".elite-navbar details.nav-menu, .elite-navbar details.nav-system"' in script
    assert "if (other !== menu) other.open = false" in script
    assert 'event.target.closest(".elite-navbar details")' in script
    assert 'event.key !== "Escape"' in script
