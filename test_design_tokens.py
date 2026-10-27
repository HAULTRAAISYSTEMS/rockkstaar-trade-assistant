"""One rule needs one spelling.

Across this week the app gained a single visual rule: SOLID means the app
measured or filed a value, DASHED means it does not fully know. It now
appears on the quarter drill, the swing grade badge, the earnings calendar,
the stock-detail stat cards, the terminal rail and symbol stamp, the Intel KPI
tiles and the price-alert badges — eight surfaces, written with a different
literal each time (#f0b53f here, var(--warn-500) there, #6d7783 in a third
place). That is how a rule quietly stops being one rule.

Separately, style.css had grown a second up/down/caution palette beside its
own: Tailwind's greens, reds and ambers as raw literals, 218 of them. Two
vocabularies for one meaning is how "the app's green" stops having an answer.

These tests hold the vocabulary to one spelling. They check names and
definitions, never rendered pixels — the substitutions preserved every value
exactly, so nothing here asserts a colour is pretty, only that there is one
place to change it.
"""
import re
from pathlib import Path

import pytest


CSS_DIR = Path("static/css")
STYLE = (CSS_DIR / "style.css").read_text()
ROOT_BLOCK = STYLE[:STYLE.index("/* Signature gradient")]

TEMPLATES = {p.name: p.read_text() for p in Path("templates").glob("*.html")}
ALL_CSS = {p.name: p.read_text() for p in CSS_DIR.glob("*.css")}

TRUST_TOKENS = ("known-dot", "known-dot-halo", "unknown-ink",
                "unknown-line", "unknown-accent", "caveat-rule", "caveat-wash")


def _defined_tokens() -> set:
    names = set()
    for src in ALL_CSS.values():
        names |= set(re.findall(r"--([a-z0-9-]+)\s*:", src))
    return names


class TestTheTrustVocabularyExists:
    @pytest.mark.parametrize("name", TRUST_TOKENS)
    def test_each_token_is_defined_once(self, name):
        hits = re.findall(rf"^\s*--{name}\s*:", ROOT_BLOCK, re.M)
        assert len(hits) == 1, f"--{name} defined {len(hits)} times"

    def test_the_rule_is_written_down_next_to_them(self):
        """A token named --unknown-line with no explanation is a colour. The
        comment is what makes it a rule the next change has to respect."""
        assert "SOLID means the app measured or filed this" in ROOT_BLOCK
        assert "DASHED means it does not fully know" in ROOT_BLOCK

    def test_the_known_dot_is_the_apps_positive_colour_not_a_new_green(self):
        assert "--known-dot:var(--pos-500)" in ROOT_BLOCK


class TestTheSurfacesUseTheTokensRatherThanTheirOwnLiterals:
    @pytest.mark.parametrize("where, needle", [
        ("style.css",          "background: var(--known-dot)"),
        ("style.css",          "border: 1px dashed var(--unknown-line)"),
        ("style.css",          "border-left: 1px dashed var(--caveat-rule)"),
        ("terminal.html",      "var(--unknown-accent)"),
        ("terminal.html",      "border: 1px dashed var(--unknown-line)"),
        ("intel.html",         "var(--unknown-line"),
        ("_liq_scripts.html",  "var(--unknown-ink"),
        ("_liq_scripts.html",  "var(--unknown-line"),
    ])
    def test_it_is_spelled_with_the_token(self, where, needle):
        src = STYLE if where == "style.css" else TEMPLATES[where]
        assert needle in src, f"{where} does not use {needle}"

    def test_the_old_literal_for_the_caveat_rule_is_gone_from_style_css(self):
        """It was `var(--elite-gold, #f0b53f)` in two places and a bare
        #f0b53f in a third."""
        body = STYLE[STYLE.index("/* Signature gradient"):]
        assert "dashed var(--elite-gold, #f0b53f)" not in body


class TestOneGreenOneRedOneAmber:
    DUPES = {"4ade80": "pos-400", "22c55e": "pos-450",
             "f87171": "neg-400", "ef4444": "neg-450",
             "fbbf24": "warn-400", "f59e0b": "warn-450"}

    @pytest.mark.parametrize("literal, token", list(DUPES.items()))
    def test_the_literal_survives_only_as_its_own_definition(self, literal, token):
        """Named, not restyled: the value is unchanged, so nothing moves on
        screen — it just has one place to change now."""
        assert f"--{token}:#{literal}" in ROOT_BLOCK
        body = STYLE[STYLE.index("/* ── Trust vocabulary"):]
        assert not re.findall(rf"#{literal}(?![0-9a-fA-F])", body, re.I)

    def test_the_legacy_brand_orange_is_named_but_deliberately_unchanged(self):
        """The --orange-* names were repointed to gold when Elite landed and
        these literals were not, so the setup and swing chips are the last UI
        still in the old brand colour. Naming them makes that one edit;
        making the edit is the user's call, not a side effect of tidying."""
        for token, literal in [("legacy-orange-300", "FFC078"),
                               ("legacy-orange-400", "FFA94D"),
                               ("legacy-orange-500", "f97316"),
                               ("legacy-orange-550", "fb923c"),
                               ("legacy-orange-600", "FF7A18")]:
            assert f"--{token}:#{literal}" in ROOT_BLOCK
        assert "Named, not changed" in ROOT_BLOCK

    def test_no_legacy_orange_literal_is_left_loose_in_the_body(self):
        body = STYLE[STYLE.index("/* ── Trust vocabulary"):]
        for literal in ("FFC078", "FFA94D", "f97316", "fb923c", "FF7A18"):
            assert not re.findall(rf"#{literal}(?![0-9a-fA-F])", body, re.I), literal


class TestNothingReferencesATokenThatDoesNotExist:
    """A var() with no fallback and no definition renders as nothing at all,
    which on a border or a colour is an invisible element rather than an
    obvious error."""

    def test_every_semantic_var_without_a_fallback_resolves(self):
        defined = _defined_tokens()
        prefixes = ("pos-", "neg-", "warn-", "info-", "known-",
                    "unknown-", "caveat-", "legacy-orange-")
        missing = {}
        for name, src in {**ALL_CSS, **TEMPLATES}.items():
            for tok in re.findall(r"var\(--([a-z0-9-]+)\)", src):
                if tok.startswith(prefixes) and tok not in defined:
                    missing.setdefault(tok, set()).add(name)
        assert not missing, missing

    def test_the_stylesheet_still_parses_as_balanced_blocks(self):
        assert STYLE.count("{") == STYLE.count("}")

    def test_no_substitution_left_a_hash_in_front_of_a_token(self):
        assert "#var(" not in STYLE

    def test_no_substitution_swallowed_a_following_hex_digit(self):
        assert not re.findall(r"var\(--(?:pos|neg|warn|legacy-orange)-[0-9]+\)[0-9a-fA-F]", STYLE)
