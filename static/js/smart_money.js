(function () {
  "use strict";

  function selectPanel(name) {
    document.querySelectorAll(".sm-tab").forEach(function (tab) {
      var active = tab.dataset.panel === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".sm-panel").forEach(function (panel) {
      panel.hidden = panel.id !== "sm-" + name;
    });
  }

  function selectSummary(days) {
    document.querySelectorAll("[data-summary-days]").forEach(function (grid) {
      grid.classList.toggle("is-active", grid.dataset.summaryDays === days);
    });
    document.querySelectorAll("[data-summary-target]").forEach(function (button) {
      button.classList.toggle("active", button.dataset.summaryTarget === days);
    });
  }

  document.addEventListener("click", function (event) {
    var tab = event.target.closest(".sm-tab[data-panel]");
    if (tab) {
      selectPanel(tab.dataset.panel);
      return;
    }
    var range = event.target.closest("[data-summary-target]");
    if (range) selectSummary(range.dataset.summaryTarget);
  });

  selectSummary("30");
})();

/* Insider track record: this insider's stored buys and how they've done since. */
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".sm-track"); if (!btn) return;
    var box = btn.closest(".sm-buy").querySelector(".sm-track-box");
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false; box.innerHTML = "<small>Loading this insider's record…</small>";
    fetch("/api/insider/track/" + encodeURIComponent(btn.dataset.cik), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (!d.ok || !d.count) { box.innerHTML = "<small>No other buys by this insider in the stored data yet.</small>"; return; }
        box.innerHTML = "<b>" + d.count + " buy" + (d.count === 1 ? "" : "s") + " on record" +
          (d.avg_since_pct != null ? " · avg " + (d.avg_since_pct >= 0 ? "+" : "") + d.avg_since_pct + "% since · " + d.winners + " up" : "") + "</b><ul>" +
          d.buys.slice(0, 6).map(function (b) {
            return "<li>" + esc(b.ticker) + " · " + esc(b.trade_date) + " · $" + Math.round(b.value || 0).toLocaleString() +
              (b.since_pct != null ? ' · <span class="' + (b.since_pct >= 0 ? "sm-positive" : "sm-negative") + '">' + (b.since_pct >= 0 ? "+" : "") + b.since_pct + "%</span>" : "") + "</li>";
          }).join("") + "</ul>";
      }).catch(function () { box.innerHTML = "<small>Track record unavailable.</small>"; });
  });
})();
