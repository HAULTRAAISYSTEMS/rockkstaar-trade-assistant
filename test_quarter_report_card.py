"""Seven checks report card: layout pieces that must stay wired."""
from pathlib import Path

PAGE = Path("templates/fundamentals.html").read_text()
CSS = Path("static/css/quarter_report.css").read_text()


def test_command_bar_has_ticker_quarter_picker_and_walk():
    bar = PAGE[PAGE.index('<div class="qd-id qx-bar">'):]
    bar = bar[:bar.index("</div>")]
    assert 'id="q-co"' in bar and 'id="q-pick"' in bar and "qdWalk()" in bar
    assert "qxPickQuarter(this.value)" in bar


def test_intro_is_behind_how_this_works():
    how = PAGE[PAGE.index('<section class="qx-how" id="q-how" hidden>'):]
    assert "Seven checks on one quarter" not in how[:200]
    assert how.index("Show me where") < how.index("</section>")


def test_report_card_pieces_exist():
    for fn in ("function qxRing(", "function qxVerdict(", "function qxTiles(", "function qxCompare(",
               "function qxSpark(", "function qxDrawer(", "function qxPractice(", "function qxFillQuarters("):
        assert fn in PAGE, fn
    assert "'/api/quarter/walkthrough/' + encodeURIComponent(ticker) + (end ? '?end='" in PAGE
    assert 'id="qd-ck-' in PAGE and "qxOpenCheck(" in PAGE


def test_drawer_is_lifted_above_the_nav():
    assert "portal.id = 'qx-portal'" in PAGE
    assert ":is(#tab-quarter, #qx-portal) .qx-drawer { position: fixed; z-index: 10001;" in CSS
    assert "quarter_report.css" in PAGE


def test_saved_cards_carry_per_check_verdicts(monkeypatch):
    import app as legacy
    card = legacy._quarter_card({"id": 1, "ticker": "GOOG", "period": "Quarter ended 2026-09-30",
                                 "figures": {"rev": 100, "revP": 80, "cfo": 50, "niy": 60}})
    assert len(card["checks"]) == 7 and {"key", "verdict", "value", "applies"} <= set(card["checks"][0])


def test_cards_use_the_same_glass_as_mcff():
    for needle in ("class=\"qx-hero wa-card\"", "class=\"qx-tile wa-card is-'", "class=\"qd-glance wa-card\"",
                   "class=\"qd-item qd-pe wa-card\"", "class=\"qd-item wa-card\" id=\"qd-ck-'"):
        assert needle in PAGE, needle
    assert ":is(#tab-quarter, #qx-portal) .wa-card { border-radius: 18px !important; }" in CSS
