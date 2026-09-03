"""A typo in a price field must not be a 500.

entry_price and exit_price went straight into a bare float(). Anything that
wasn't a number - "$182.50", "1,240", an empty-looking field with a
non-breaking space, or a genuine typo - raised ValueError inside the request
and returned the error page with the trade unsaved and the form cleared.
"""
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-key")

import app as legacy


def parse(**fields):
    return legacy._parse_journal_form(fields)


class TestPricesPeopleActuallyType:
    @pytest.mark.parametrize("typed,expected", [
        ("182.50", 182.50),
        ("$182.50", 182.50),
        ("1,240", 1240.0),
        ("$1,240.75", 1240.75),
        (" 47.90 ", 47.90),
        (" 47.90", 47.90),
    ])
    def test_a_price_is_read_the_way_it_was_written(self, typed, expected):
        assert parse(entry_price=typed)["entry_price"] == pytest.approx(expected)

    def test_an_empty_price_is_zero_not_an_error(self):
        assert parse(entry_price="", exit_price="")["entry_price"] == 0

    def test_a_field_of_only_spaces_counts_as_empty(self):
        assert parse(stop_price="   ")["stop_price"] is None


class TestATypoIsReportedNotRaised:
    def test_the_error_names_the_field(self):
        with pytest.raises(legacy.JournalFormError) as caught:
            parse(entry_price="18o.50")
        assert "Entry price" in str(caught.value)

    def test_the_error_quotes_what_was_typed(self):
        with pytest.raises(legacy.JournalFormError) as caught:
            parse(exit_price="abc")
        assert "abc" in str(caught.value)

    @pytest.mark.parametrize("field,label", [
        ("entry_price", "Entry price"),
        ("exit_price", "Exit price"),
        ("stop_price", "Stop price"),
        ("option_premium", "Option premium"),
        ("shares", "Shares"),
        ("contracts", "Contracts"),
        ("momentum_score", "Momentum score"),
    ])
    def test_every_number_field_is_covered(self, field, label):
        with pytest.raises(legacy.JournalFormError) as caught:
            parse(**{field: "not a number"})
        assert label in str(caught.value)

    def test_a_decimal_share_count_is_accepted_as_a_whole_number(self):
        """Some brokers export 100.0, which int() alone rejects."""
        assert parse(shares="100.0")["shares"] == 100


class TestThroughTheRoute:
    @pytest.fixture
    def client(self):
        return legacy.app.test_client()

    def test_a_bad_price_does_not_return_a_server_error(self, client):
        response = client.post("/journal/add",
                               data={"ticker": "NVDA", "entry_price": "18o.50"})
        assert response.status_code != 500

    def test_the_same_for_an_edit(self, client):
        response = client.post("/journal/1/edit",
                               data={"ticker": "NVDA", "entry_price": "18o.50"})
        assert response.status_code != 500
