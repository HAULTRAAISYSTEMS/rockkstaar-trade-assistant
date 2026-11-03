"""Two ways this app stopped scrolling, and neither was a scroll handler.

A page scrolls when the viewport is the thing that scrolls. Both faults here
put something else in the way.

ONE — the root element.

    html, body { overflow-x: hidden; }

`overflow-x: hidden` makes the root a scroll container, which traps Mac wheel
and trackpad gestures. That was spotted and changed to `overflow-x: clip`,
which does not create a scroll container — in Chromium. `overflow` on the
ROOT element propagates to the viewport, and `clip` there is the least
interoperable corner of the property: a root that ends up clipping both axes
cannot scroll at all. Every device in the report was Safari; every browser
available for testing here is Chromium, which is why it kept passing.

Neither value was needed. The only thing overflowing horizontally was the
ticker tape, and .market-tape and .tw-tape already clip their own content.
With the declaration gone, scrollWidth equals the viewport width on every
page at phone, tablet and desktop widths.

TWO — the terminal's watchlist rail.

    .tw-wl { max-height: 360px; overflow-y: auto; }   /* ≤780px */

In three columns that is an ordinary sidebar list: it scrolls under the
cursor, the page scrolls everywhere else. Collapsed into one column it spans
the whole screen, so a finger lands on it wherever you touch, the list eats
the gesture, and the page behind never moves. Measured at 390px, a 700px
wheel over the rail moved the page 0px.

These are style assertions, not rendering tests — the browsers needed to
prove it properly will not install here. The measurements above came from a
real Chromium at five viewports; what these keep is the shape of the fix.
"""
import re
from pathlib import Path

import pytest


STYLE = Path("static/css/style.css").read_text()
ELITE = Path("static/css/elite.css").read_text()
TERMINAL = Path("templates/terminal.html").read_text()


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def _rule(css: str, selector: str) -> str:
    """The body of the first top-level rule for an exact selector.

    Comments are stripped first: these rules carry long explanations that
    quote the very declarations being asserted absent.
    """
    m = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    return _strip_comments(m.group(1)) if m else ""


class TestTheRootIsNotAScrollContainer:
    @pytest.mark.parametrize("selector", ["html", "body"])
    def test_neither_clips_nor_hides_horizontally(self, selector):
        """`hidden` traps wheel gestures; `clip` on the root is the version
        the engines disagree about. Neither belongs here."""
        body = _rule(STYLE, selector)
        assert body, f"no top-level {selector} rule found"
        assert "overflow-x" not in body, f"{selector} declares overflow-x again"
        assert "overflow:" not in body, f"{selector} declares shorthand overflow"

    def test_the_reason_is_recorded_where_the_next_person_will_look(self):
        """This has now been changed three times. The comment is what stops a
        fourth from reaching for `hidden` again."""
        assert "No overflow-x here, deliberately" in STYLE

    def test_the_tape_still_clips_itself(self):
        """Which is what makes the root declaration unnecessary — if these
        ever stop clipping, the horizontal overflow comes back and someone
        will be tempted to fix it at the root again."""
        assert "overflow: hidden" in _rule(ELITE, ".market-tape")
        assert "overflow: hidden" in _rule(TERMINAL, ".tw-tape")

    def test_pull_to_refresh_damping_stays_off_the_desktop(self):
        """overscroll-behavior-y: none on the root belongs to touch devices
        that use the custom pull-to-refresh, not to Mac trackpads."""
        i = ELITE.find("overscroll-behavior-y: none")
        assert i != -1
        assert "(hover: none) and (pointer: coarse)" in ELITE[max(0, i - 400):i]


class TestTheWatchlistRailDoesNotEatThePage:
    PHONE = TERMINAL[TERMINAL.index("@media (max-width: 780px)"):]

    def test_in_one_column_the_rail_is_not_its_own_scroller(self):
        rail = _rule(self.PHONE, ".tw-wl")
        assert "max-height: none" in rail
        assert "overflow-y: visible" in rail

    def test_the_trapping_height_is_gone(self):
        assert "max-height: 360px" not in _strip_comments(self.PHONE)

    def test_it_is_still_a_scroller_in_the_multi_column_layout(self):
        """The fix is scoped. A sidebar beside page content is supposed to
        scroll under the cursor — undoing that would be a different bug."""
        assert "max-height: calc(100dvh - 140px)" in TERMINAL
        assert "overflow-y:auto" in _rule(TERMINAL, ".tw-wl")

    def test_the_phone_rule_comes_after_the_base_rule(self):
        """Same specificity, so source order is what makes it win."""
        assert TERMINAL.index(".tw-wl { max-height: 660px") < \
            TERMINAL.index("@media (max-width: 780px)")

    def test_the_reason_is_written_down(self):
        assert "must not be its own scroller here" in TERMINAL
