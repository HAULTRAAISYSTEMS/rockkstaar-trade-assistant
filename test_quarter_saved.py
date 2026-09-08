"""Saved quarters.

Reading a filing takes twenty minutes. Losing it to a page refresh — or
retyping it to compare this quarter against the last one — is what stops the
drill from becoming a habit.

Only the typed figures are stored, never the grades. Grades are derived, and
a threshold that moves later should reflow every saved quarter rather than
leaving a museum of verdicts computed under rules the app no longer uses.
"""
import json
import re

import pytest

import database as db


META = {"rev": "60801", "cogs": "11330", "niy": "42621", "cfo": "64088"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A database of this test's own, so saves never touch the dev one."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    import migration_runner
    migration_runner.run_migrations()
    return db


class TestSaving:
    def test_a_quarter_comes_back_as_it_went_in(self, store):
        store.save_quarter_entry("META", "Q3 FY26", META)
        [entry] = store.get_quarter_entries()
        assert entry["ticker"] == "META"
        assert entry["period"] == "Q3 FY26"
        assert entry["figures"] == META

    def test_the_ticker_is_normalised_so_it_can_be_found_again(self, store):
        store.save_quarter_entry("  meta ", "Q3", META)
        assert store.get_quarter_entries()[0]["ticker"] == "META"

    def test_saving_the_same_quarter_again_edits_it(self, store):
        """Correcting a misread figure is not a second copy of the quarter."""
        first = store.save_quarter_entry("META", "Q3", META)
        again = store.save_quarter_entry("META", "Q3", dict(META, cogs="11331"))
        assert first == again
        entries = store.get_quarter_entries()
        assert len(entries) == 1
        assert entries[0]["figures"]["cogs"] == "11331"

    def test_a_different_quarter_of_the_same_company_is_its_own_row(self, store):
        store.save_quarter_entry("META", "Q3", META)
        store.save_quarter_entry("META", "Q2", {"rev": "1"})
        assert len(store.get_quarter_entries()) == 2

    def test_blank_fields_are_not_stored(self, store):
        """A skipped check should stay skipped when the quarter is reopened."""
        store.save_quarter_entry("META", "Q3", dict(META, opex="", eps=None))
        figures = store.get_quarter_entries()[0]["figures"]
        assert "opex" not in figures and "eps" not in figures

    def test_a_save_without_a_ticker_is_refused(self, store):
        """The ticker is how it is found again."""
        with pytest.raises(ValueError):
            store.save_quarter_entry("  ", "Q3", META)

    def test_the_newest_touched_quarter_leads_the_list(self, store):
        """Three saves inside one second still order correctly.

        A second-precision timestamp ties here, and correcting a figure and
        saving again is precisely the case that lands inside one second.
        """
        store.save_quarter_entry("AAPL", "Q3", {"rev": "1"})
        store.save_quarter_entry("META", "Q3", META)
        store.save_quarter_entry("AAPL", "Q3", {"rev": "2"})   # touched last
        assert store.get_quarter_entries()[0]["ticker"] == "AAPL"


class TestItBelongsToOneReader:
    def test_another_user_cannot_open_it_by_id(self, store):
        entry_id = store.save_quarter_entry("META", "Q3", META, user_id=1)
        assert store.get_quarter_entry(entry_id, user_id=1)
        assert store.get_quarter_entry(entry_id, user_id=2) is None

    def test_another_user_cannot_delete_it(self, store):
        entry_id = store.save_quarter_entry("META", "Q3", META, user_id=1)
        assert store.delete_quarter_entry(entry_id, user_id=2) is False
        assert store.get_quarter_entry(entry_id, user_id=1)

    def test_two_readers_keep_their_own_copy_of_the_same_quarter(self, store):
        store.save_quarter_entry("META", "Q3", META, user_id=1)
        store.save_quarter_entry("META", "Q3", {"rev": "9"}, user_id=2)
        assert store.get_quarter_entries(user_id=1)[0]["figures"] == META
        assert store.get_quarter_entries(user_id=2)[0]["figures"] == {"rev": "9"}


class TestDeleting:
    def test_it_goes(self, store):
        entry_id = store.save_quarter_entry("META", "Q3", META)
        assert store.delete_quarter_entry(entry_id) is True
        assert store.get_quarter_entries() == []

    def test_deleting_it_twice_is_not_an_error(self, store):
        entry_id = store.save_quarter_entry("META", "Q3", META)
        store.delete_quarter_entry(entry_id)
        assert store.delete_quarter_entry(entry_id) is False


class TestUnreadableRows:
    def test_a_row_this_app_cannot_parse_is_listed_so_it_can_be_deleted(self, store):
        """Junk must not carry into the form, but must not hide the row either."""
        store.save_quarter_entry("META", "Q3", META)
        conn = store.get_db()
        conn.execute("UPDATE quarter_entries SET figures = ?", ("{not json",))
        conn.commit()
        conn.close()
        [entry] = store.get_quarter_entries()
        assert entry["figures"] == {}
        assert entry["ticker"] == "META"


# ── Through the app ───────────────────────────────────────────────────────────

@pytest.fixture
def client(store):
    from unittest.mock import patch
    import web_app, app as _app
    c = web_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = 1
        sess["logged_in"] = True
    with patch.object(_app, "_auth_required", lambda *a, **k: False):
        yield c


def csrf(client):
    page = client.get("/fundamentals?tab=quarter").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


class TestTheEndpoints:
    def test_save_then_list_then_reopen(self, client):
        token = csrf(client)
        saved = client.post("/api/quarter/saved",
                            json={"ticker": "meta", "period": "Q3 FY26", "figures": META},
                            headers={"X-CSRFToken": token}).get_json()
        assert saved["entries"][0]["ticker"] == "META"

        listed = client.get("/api/quarter/saved").get_json()["entries"]
        assert len(listed) == 1

        opened = client.get("/api/quarter/saved/" + str(saved["id"])).get_json()
        assert opened["figures"] == META

    def test_the_list_carries_the_grades_recomputed_not_stored(self, client):
        """Nothing about a verdict is persisted; it is derived on the way out."""
        client.post("/api/quarter/saved",
                    json={"ticker": "META", "period": "Q3", "figures": META},
                    headers={"X-CSRFToken": csrf(client)})
        card = client.get("/api/quarter/saved").get_json()["entries"][0]
        assert card["counts"]["clean"] >= 1
        conn = db.get_db()
        stored = conn.execute("SELECT figures FROM quarter_entries").fetchone()["figures"]
        conn.close()
        assert "clean" not in stored and "verdict" not in stored

    def test_a_save_with_no_ticker_says_why(self, client):
        resp = client.post("/api/quarter/saved", json={"figures": META},
                           headers={"X-CSRFToken": csrf(client)})
        assert resp.status_code == 400
        assert "ticker" in resp.get_json()["error"].lower()

    def test_an_empty_save_says_why(self, client):
        resp = client.post("/api/quarter/saved", json={"ticker": "META", "figures": {}},
                           headers={"X-CSRFToken": csrf(client)})
        assert resp.status_code == 400

    def test_fields_the_drill_does_not_use_are_dropped(self, client):
        """The form posts what it collects; the store keeps what it knows."""
        client.post("/api/quarter/saved",
                    json={"ticker": "META", "figures": dict(META, sneaky="x")},
                    headers={"X-CSRFToken": csrf(client)})
        opened = client.get("/api/quarter/saved").get_json()["entries"][0]
        entry = client.get("/api/quarter/saved/" + str(opened["id"])).get_json()
        assert "sneaky" not in entry["figures"]

    def test_reopening_something_that_is_not_there(self, client):
        assert client.get("/api/quarter/saved/9999").status_code == 404

    def test_delete_returns_the_shortened_list(self, client):
        token = csrf(client)
        saved = client.post("/api/quarter/saved",
                            json={"ticker": "META", "figures": META},
                            headers={"X-CSRFToken": token}).get_json()
        out = client.delete("/api/quarter/saved/" + str(saved["id"]),
                            headers={"X-CSRFToken": token})
        assert out.status_code == 200
        assert out.get_json()["entries"] == []

    def test_saving_needs_the_csrf_token(self, client):
        """A save is a write; it is protected like every other write."""
        assert client.post("/api/quarter/saved",
                           json={"ticker": "META", "figures": META}).status_code == 400

    def test_the_page_offers_the_save(self, client):
        page = client.get("/fundamentals?tab=quarter").get_data(as_text=True)
        assert "Save this quarter" in page
        assert 'id="q-saved"' in page


class TestMigrationsRunThemselves:
    """The two existing migrations were applied by hand.

    That is a deploy step nobody remembers, and the failure it produces is a
    table that quietly does not exist in the next environment — which is what
    the saved-quarters table would have hit on Render.
    """

    def test_startup_runs_them(self):
        from pathlib import Path
        src = Path("app.py").read_text()
        boot = src[src.index("    init_db()"):][:1400]
        assert "run_migrations" in boot

    def test_a_failed_migration_does_not_crash_the_process(self):
        """The app served every page before migrations ran at boot.

        A crash loop is worse than a missing feature.
        """
        from pathlib import Path
        src = Path("app.py").read_text()
        boot = src[src.index("from migration_runner import run_migrations"):][:600]
        assert "except Exception" in boot
        assert "raise" not in boot

    def test_every_migration_is_registered_in_order(self):
        """Naming the newest one pinned this to a moment rather than to the
        rule. A migration file that nobody registers creates no table, and
        the first thing anyone hears about it is a missing-table error in
        production."""
        from pathlib import Path
        import migration_runner
        on_disk = sorted(p.stem for p in Path("migrations").glob("m[0-9]*.py"))
        registered = [name.split(".")[-1] for name in migration_runner.MIGRATIONS]
        assert registered == on_disk

    def test_running_them_twice_applies_nothing_the_second_time(self, store):
        import migration_runner
        assert migration_runner.run_migrations() == []
