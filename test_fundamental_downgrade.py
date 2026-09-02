"""Auto-downgrade triggers on the 40-point fundamental scorecard.

The rubric has always said any trigger drops the verdict a full band, but the
verdict was computed from the score alone and red_flags was only ever read by
the narrative text builder. A company could report a goodwill impairment and
FCF below net income and still print "Great Company".
"""
import pytest

import fundamentals_engine as fe


def flags(*keys):
    return [{"key": k, "label": fe.RED_FLAG_DEFS.get(k, k)} for k in keys]


@pytest.mark.parametrize("verdict,expected", [
    ("Great Company", "Good"),
    ("Good", "Caution"),
    ("Caution", "Avoid"),
    ("Avoid", "Avoid"),
])
def test_a_trigger_drops_exactly_one_band(verdict, expected):
    result, fired = fe.apply_downgrade(verdict, flags("goodwill_impairment"))
    assert result == expected
    assert len(fired) == 1


def test_several_triggers_still_drop_only_one_band():
    """One band, not one per trigger - the score already reflects the weakness."""
    result, fired = fe.apply_downgrade(
        "Great Company", flags("goodwill_impairment", "income_positive_fcf_negative",
                               "debt_growing_faster_than_revenue"))
    assert result == "Good" and len(fired) == 3


def test_clean_company_is_not_downgraded():
    assert fe.apply_downgrade("Great Company", [])[0] == "Great Company"


def test_current_ratio_warning_does_not_downgrade():
    """Liquidity is already scored in Section 1; downgrading would double-count."""
    result, fired = fe.apply_downgrade("Great Company", flags("current_ratio_below_1"))
    assert result == "Great Company" and fired == []


@pytest.mark.parametrize("key", sorted(fe.DOWNGRADE_TRIGGERS))
def test_every_declared_trigger_actually_fires(key):
    assert fe.apply_downgrade("Great Company", flags(key))[0] == "Good"


def test_every_trigger_has_an_explanation():
    assert fe.DOWNGRADE_TRIGGERS <= set(fe.RED_FLAG_DEFS), "a trigger with no wording cannot be explained"


def test_unknown_verdict_is_left_alone():
    assert fe.apply_downgrade("Unrated", flags("goodwill_impairment"))[0] == "Unrated"


def test_bands_are_ordered_worst_first():
    assert fe.VERDICT_BANDS == ["Avoid", "Caution", "Good", "Great Company"]
