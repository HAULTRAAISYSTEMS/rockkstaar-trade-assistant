"""Phase 3 integration for Live Research on the existing stock-detail experience.

The legacy stock detail route is intentionally left untouched.  This hook augments only
successful HTML responses for /stock/<ticker>, inserting a Research anchor/panel and
loading the Phase 2 research presentation assets.  Published data is still sourced
exclusively through the Phase 2 service/API, so drafts cannot leak into this view.
"""
from __future__ import annotations
import html
import re

_STOCK_PATH = re.compile(r"^/stock/([A-Za-z0-9.\-]+)$")


def install_stock_research(app):
    @app.after_request
    def add_stock_research(response):
        match = _STOCK_PATH.match(__import__('flask').request.path)
        if not match or response.status_code != 200 or not response.mimetype.startswith('text/html'):
            return response
        body = response.get_data(as_text=True)
        if 'id="stock-research-panel"' in body:
            return response
        ticker = html.escape(match.group(1).upper(), quote=True)
        panel = f'''\n<section id="stock-research-panel" class="detail-card lr-stock-panel" data-ticker="{ticker}">
  <div class="lr-stock-head">
    <div><span class="lr-kicker">TRADESTAAR LIVE RESEARCH</span><h3 class="card-title">Research</h3></div>
    <div class="lr-stock-actions"><a href="/live-research?ticker={ticker}">Open full feed</a><button type="button" class="lr-alert" data-ticker="{ticker}" data-enabled="0">🔕 Research alerts off</button></div>
  </div>
  <div class="stock-research-feed"><div class="lr-empty">Loading published research…</div></div>
</section>\n'''
        # Add a Research tab to the existing mobile stock navigation when present.
        body = body.replace('</nav>', f'<a href="#stock-research-panel">Research</a></nav>', 1)
        # Keep the existing stock page intact; append Research as another detail card.
        marker = '</div>\n\n{% endblock %}'
        if '</main>' in body:
            body = body.replace('</main>', panel + '</main>', 1)
        else:
            body += panel
        assets = '<link rel="stylesheet" href="/static/css/live_research.css"><script src="/static/js/stock_research.js" defer></script>'
        body = body.replace('</head>', assets + '</head>', 1)
        response.set_data(body)
        response.content_length = len(response.get_data())
        return response
    return app
