from pathlib import Path


ROOT = Path(__file__).parent


def test_breaking_section_is_a_fresh_source_feed_not_stale_published_priority():
    template = (ROOT / "templates/live_research.html").read_text(encoding="utf-8")
    assert "Breaking Sources" in template
    assert "Latest attributed headlines from the last 24 hours" in template
    assert "Open and verify source" in template
    assert "Fresh published Critical, High and Breaking intelligence" not in template


def test_live_headline_poll_does_not_mix_unreviewed_rows_into_published_feed():
    script = (ROOT / "static/js/live_research.js").read_text(encoding="utf-8")
    assert 'fetch("/api/live-research/headlines?"' in script
    assert "renderHeadlines(payload.headlines || [])" in script
    assert "featuredFeed.appendChild(researchItem(post, true))" not in script
