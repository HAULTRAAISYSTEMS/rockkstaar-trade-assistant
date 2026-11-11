/* Catalyst heat map, explain-this-move and earnings reaction replay. */
(function () {
  "use strict";
  if (window.TSNewsDesk) return;
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function getJSON(url) { return fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" }).then(function (r) { return r.json(); }); }
  function pct(v) { return v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(2) + "%"; }

  /* ── Explain this move ── */
  function explain(ticker, box) {
    box.hidden = false;
    box.innerHTML = '<div class="nd-x-card"><span class="nd-kicker">⚡ Why is ' + esc(ticker) + ' moving?</span><p class="nd-x-load">Checking the tape, the sector and the news…</p></div>';
    getJSON("/api/news-desk/explain/" + encodeURIComponent(ticker)).then(function (d) {
      if (!d || !d.ok) { box.querySelector(".nd-x-load").textContent = "Could not explain the move right now."; return; }
      var f = d.facts || {}, lines = d.ai && d.ai_lines ? d.ai_lines : [d.headline, d.detail];
      box.innerHTML = '<div class="nd-x-card nd-drv-' + esc(d.driver) + '"><div class="nd-x-head"><span class="nd-kicker">⚡ Why is ' + esc(ticker) + " moving?</span>" +
        '<button type="button" class="nd-x-close" aria-label="Close">×</button></div>' +
        lines.filter(Boolean).map(function (l, i) { return "<p" + (i === 0 ? ' class="nd-x-lead"' : "") + ">" + esc(l) + "</p>"; }).join("") +
        '<div class="nd-x-chips"><span>' + esc(ticker) + " " + pct(f.change_pct) + "</span><span>SPY " + pct(f.spy_pct) + "</span>" +
        (f.sector_etf ? "<span>" + esc(f.sector_etf) + " " + pct(f.sector_pct) + "</span>" : "") +
        (f.rel_volume ? "<span>" + Number(f.rel_volume).toFixed(1) + "× vol</span>" : "") + "</div>" +
        ((f.news || []).length ? '<div class="nd-x-news">' + f.news.map(function (n) {
          return (n.url ? '<a href="' + esc(n.url) + '" target="_blank" rel="noopener noreferrer">' : "<span>") + esc(n.headline) + " <small>" + esc([n.source, n.time].filter(Boolean).join(" · ")) + "</small>" + (n.url ? "</a>" : "</span>");
        }).join("") + "</div>" : "") +
        '<small class="nd-x-foot">' + (d.ai ? "AI read of the facts above" : "Rule-based read of the facts above") + ' · <a href="/stock/' + encodeURIComponent(ticker) + '">Open ' + esc(ticker) + " →</a>" +
        (document.getElementById("intel-news") ? ' · <a href="#intel-ticker-news" class="nd-x-stories">All ' + esc(ticker) + " stories ↓</a>" : "") + "</small></div>";
      var x = box.querySelector(".nd-x-close"); if (x) x.addEventListener("click", function () { box.hidden = true; });
    }).catch(function () { box.innerHTML = ""; box.hidden = true; });
  }

  /* ── Heat map ── */
  function heat() {
    var grid = document.getElementById("nd-grid"); if (!grid) return;
    var meta = document.getElementById("nd-heat-meta"), panel = document.getElementById("nd-explain"), retry = null;
    function color(t) {
      if (t.label === "PENDING") return "background:#10161d";
      var a = 0.1 + Math.min(1, (t.intensity || 0) / 100) * 0.55;
      if (t.label === "BULLISH") return "background:rgba(50,212,155," + a + ")";
      if (t.label === "BEARISH") return "background:rgba(255,105,115," + a + ")";
      return "background:rgba(140,155,170," + (a * 0.6) + ")";
    }
    function load() {
      getJSON("/api/news-desk/heatmap").then(function (d) {
        if (!d || !d.ok) { meta.textContent = "Heat map unavailable."; return; }
        grid.innerHTML = d.tiles.map(function (t) {
          var chg = t.change_pct == null ? "" : '<em class="' + (t.change_pct >= 0 ? "up" : "dn") + '">' + pct(t.change_pct) + "</em>";
          return '<button type="button" class="nd-tile ' + t.label.toLowerCase() + '" style="' + color(t) + '" data-t="' + esc(t.ticker) + '" title="' + esc(t.top || "") + '">' +
            "<b>" + esc(t.ticker) + "</b>" + chg + "<span>" + (t.stories == null ? "loading…" : t.stories + " stor" + (t.stories === 1 ? "y" : "ies")) + "</span>" +
            (t.top ? "<small>" + esc(t.top) + "</small>" : "") + "</button>";
        }).join("");
        var hot = d.tiles.filter(function (t) { return t.stories; }).length;
        meta.textContent = hot + " of " + d.tiles.length + " names in the news · tap a tile for why it's moving";
        clearTimeout(retry);
        retry = setTimeout(load, d.pending && d.pending.length ? 8000 : 120000);
      }).catch(function () { meta.textContent = "Heat map unavailable."; });
    }
    grid.addEventListener("click", function (e) {
      var b = e.target.closest(".nd-tile"); if (!b) return;
      grid.querySelectorAll(".nd-tile.on").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on"); explain(b.getAttribute("data-t"), panel);
      // Let the page bring the same ticker's stories up in its news list.
      document.dispatchEvent(new CustomEvent("nd:ticker", { detail: { ticker: b.getAttribute("data-t") } }));
      if (window.innerWidth < 760) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    load();
  }

  /* ── Earnings reaction replay ── */
  function replay(ticker, box) {
    box.innerHTML = '<p class="nd-x-load">Replaying past earnings…</p>';
    getJSON("/api/news-desk/replay/" + encodeURIComponent(ticker)).then(function (d) {
      if (!d || !d.ok) { box.innerHTML = '<p class="tb-empty">' + esc((d && d.error) || "No earnings history found.") + "</p>"; return; }
      var s = d.summary;
      box.innerHTML = '<div class="nd-rp-sum"><div><small>Avg move</small><b>±' + s.avg_abs_move + '%</b></div><div><small>Up reactions</small><b>' + s.up_days + "/" + s.reports +
        '</b></div><div><small>Gap held</small><b>' + s.hold_rate + '%</b></div><div><small>Gap filled ≤5d</small><b>' + s.gap_fill_rate + '%</b></div></div>' +
        '<table class="nd-rp"><tr><th>Report</th><th>Surprise</th><th>Gap</th><th>Day</th><th>5-day</th><th>Filled</th></tr>' +
        d.reports.map(function (r) {
          function c(v) { return '<td class="' + (v > 0 ? "up" : v < 0 ? "dn" : "") + '">' + pct(v) + "</td>"; }
          return "<tr><td>" + esc(r.date) + "</td><td>" + (r.surprise_pct == null ? "—" : (r.surprise_pct > 0 ? "+" : "") + r.surprise_pct + "%") + "</td>" + c(r.gap_pct) + c(r.day_pct) + c(r.five_day_pct) + "<td>" + (r.gap_filled ? "✓" : "—") + "</td></tr>";
        }).join("") + "</table>";
    }).catch(function () { box.innerHTML = '<p class="tb-empty">Earnings history unavailable.</p>'; });
  }

  window.TSNewsDesk = { explain: explain, replay: replay, heat: heat };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", heat); else heat();
})();

/* Explain + replay widget: <div data-move-tools="NVDA"> */
(function () {
  function init() {
    document.querySelectorAll("[data-move-tools]").forEach(function (el) {
      if (el.dataset.ready) return; el.dataset.ready = "1";
      var t = el.getAttribute("data-move-tools"), panel = el.querySelector(".mt-explain");
      el.querySelector("[data-mt-explain]").addEventListener("click", function () { window.TSNewsDesk.explain(t, panel); });
      var det = el.querySelector(".mt-replay");
      det.addEventListener("toggle", function () { if (det.open && !det.dataset.loaded) { det.dataset.loaded = "1"; window.TSNewsDesk.replay(t, det.querySelector(".mt-replay-body")); } });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
