"""The fundamentals page has to look like the rest of Tradestaar.

Sections added to this page were first written with their own hex palette,
which looks fine in isolation and drifts away from the app the moment a token
changes. Colour now comes from the Elite tokens in elite.css and style.css, and
chart series carry a CSS class rather than a colour, so the palette lives in
one place.
"""
import re

CSS = open("static/css/fundamentals.css").read()
ADDED = CSS[CSS.index("══ Additions"):]

SERIES = ("s-revenue", "s-income", "s-fcf", "s-gross", "s-operating", "s-net", "s-roic")


def test_chart_geometry_carries_no_colours():
    """A hex value in the geometry module cannot be themed from CSS."""
    src = open("fundamentals_charts.py").read()
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", src)
    assert "color" not in src


def test_every_series_has_a_class_and_a_rule():
    src = open("fundamentals_charts.py").read()
    for name in SERIES:
        assert f'"{name}"' in src, f"{name} not emitted"
        assert name in ADDED, f"{name} has no CSS rule"


def test_added_styles_use_tokens_not_raw_hex():
    """Fallbacks after a comma are allowed — a bare hex is not."""
    bare = [line.strip() for line in ADDED.splitlines()
            if re.search(r"#[0-9a-fA-F]{3,8}\b", line) and "var(--" not in line]
    assert bare == [], bare


def test_the_palette_is_defined_once():
    for token in ("--series-revenue", "--series-income", "--series-fcf",
                  "--series-gross", "--series-operating", "--series-net",
                  "--series-roic"):
        assert ADDED.count(token + ":") == 1, token


def test_added_sections_use_elite_surfaces_and_radii():
    for token in ("--elite-border", "--elite-panel-inset", "--elite-copy",
                  "--elite-muted", "--elite-faint", "--elite-gold",
                  "--elite-radius", "--font-mono"):
        assert token in ADDED, token


def test_status_colours_come_from_the_shared_scale():
    for token in ("--pos-500", "--neg-500", "--warn-500"):
        assert token in ADDED, token


def test_the_template_styles_charts_by_class_not_inline_colour():
    tpl = open("templates/fundamentals.html").read()
    charts = tpl[tpl.index("<!-- Charts -->"):tpl.index("<!-- Historical table -->")]
    assert 'fill="{{' not in charts and 'stroke="{{' not in charts
    assert "{{ bar.cls }}" in charts and "{{ line.cls }}" in charts
