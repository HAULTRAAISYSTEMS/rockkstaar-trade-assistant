"""Consensus and prior on the economic calendar.

The calendar engine dropped Finnhub's estimate, previous and actual fields, so
the Intel page printed a pair of dashes and a footnote admitting they were not
wired. The fix sat on a July branch that also retired the Command Center —
which is the opposite of where the app went — so only the calendar half is
carried across, and the template markup was rewritten in the meantime.
"""
from jinja2 import Environment, FileSystemLoader, select_autoescape

import intel_engine


def render_rows(events):
    src = open("templates/intel.html").read()
    start = src.index('{% for e in events %}<div class="calendar-row"')
    end = src.index("{% endfor %}", start) + len("{% endfor %}")
    env = Environment(loader=FileSystemLoader("templates"),
                      autoescape=select_autoescape(["html"]))
    return env.from_string(src[start:end]).render(events=events)


def event(**over):
    base = {"event": "CPI y/y", "impact": "HIGH", "time": "08:30",
            "date_label": "Sep 10", "estimate": 3.2, "previous": 3.1,
            "actual": None, "unit": "%"}
    base.update(over)
    return base


def test_the_finnhub_parser_carries_consensus_through():
    """These keys were being dropped on the floor."""
    import inspect
    src = inspect.getsource(intel_engine._econ_from_finnhub)
    for key in ("estimate", "previous", "actual", "unit"):
        assert f'"{key}"' in src, key


def test_the_static_fallback_declares_the_fields_as_absent():
    """It has no consensus data, and a missing key would raise in the row."""
    import inspect
    src = inspect.getsource(intel_engine._econ_static_fallback)
    for key in ("estimate", "previous"):
        assert f'"{key}"' in src, key


def test_consensus_renders_with_its_unit():
    html = render_rows([event()])
    assert "est 3.2%" in html and "prev 3.1%" in html


def test_a_zero_estimate_is_shown_not_swallowed():
    """0.0 is a real forecast; a truthiness check would print a dash."""
    html = render_rows([event(estimate=0.0, previous=0.2)])
    assert "est 0.0%" in html


def test_a_feed_without_consensus_shows_no_line_at_all():
    """Better than a row of dashes claiming to be data."""
    html = render_rows([event(estimate=None, previous=None)])
    assert "cal-consensus" not in html
    assert "CPI y/y" in html


def test_one_side_present_still_renders():
    html = render_rows([event(previous=None)])
    assert "est 3.2%" in html and "prev —" in html
