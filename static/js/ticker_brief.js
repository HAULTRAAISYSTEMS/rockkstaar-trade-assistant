/* "Since you last looked" + thesis scorecard for any <div data-ticker-brief="NVDA">. */
(function () {
  "use strict";
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function getJSON(url, opts) { return fetch(url, Object.assign({ headers: { Accept: "application/json" }, cache: "no-store" }, opts || {})).then(function (r) { return r.json(); }); }

  function renderChanged(box, d) {
    if (!d || !d.ok) { box.innerHTML = ""; return; }
    var head = d.first_look ? "First look" : "Since you last looked · " + esc(d.since_label);
    var cls = d.move_pct == null ? "" : (d.move_pct >= 0 ? " up" : " down");
    box.innerHTML = '<div class="tb-card tb-changed' + cls + '"><span class="tb-kicker">⏱ ' + head + '</span><ul>' +
      d.lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul>" +
      (d.headlines && d.headlines.length ? '<div class="tb-heads">' + d.headlines.map(function (h) {
        return (h.url ? '<a href="' + esc(h.url) + '" target="_blank" rel="noopener noreferrer">' : "<span>") + esc(h.headline) +
               ' <small>' + esc([h.source, h.time].filter(Boolean).join(" · ")) + "</small>" + (h.url ? "</a>" : "</span>");
      }).join("") + "</div>" : "") + "</div>";
  }

  function caseHTML(c) {
    function list(a) { return "<ul>" + (a || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>"; }
    return '<div class="tb-case"><div class="tb-bull"><b>Bull case</b>' + list(c.bull) + '</div><div class="tb-bear"><b>Bear case</b>' + list(c.bear) + "</div></div>" +
      (c.verdict ? '<p class="tb-verdict">' + esc(c.verdict) + "</p>" : "") +
      (c.watch && c.watch.length ? '<p class="tb-watch"><b>Watch next:</b> ' + c.watch.map(esc).join(" · ") + "</p>" : "") +
      '<small class="tb-note">Built from your ' + esc(c.notes) + " notes only.</small>";
  }

  function renderScore(box, d, ticker) {
    if (!d || !d.ok) { box.innerHTML = ""; return; }
    if (!d.total) {
      box.innerHTML = '<div class="tb-card tb-score"><span class="tb-kicker">🧠 Thesis scorecard</span><p class="tb-empty">No ' + esc(ticker) +
        ' notes yet. Tap <b>＋ Save to Memory</b> on any news story to start your thesis.</p></div>'; return;
    }
    var c = d.counts, tot = Math.max(1, d.total), lean = { bull: "Leaning bullish", bear: "Leaning bearish", mixed: "Mixed evidence", none: "" }[d.lean];
    var bar = '<div class="tb-meter"><i class="s" style="width:' + (c.strengthened / tot * 100) + '%"></i><i class="n" style="width:' + ((c.neutral + c.uncertain) / tot * 100) + '%"></i><i class="w" style="width:' + (c.weakened / tot * 100) + '%"></i></div>';
    box.innerHTML = '<div class="tb-card tb-score lean-' + d.lean + '"><div class="tb-score-head"><span class="tb-kicker">🧠 Thesis scorecard</span><b>' + esc(lean) + "</b></div>" + bar +
      '<div class="tb-tally"><span class="s">▲ ' + c.strengthened + ' strengthened</span><span class="w">▼ ' + c.weakened + ' weakened</span><span>' + (c.neutral + c.uncertain) + " neutral/uncertain</span></div>" +
      '<div class="tb-case-box">' + (d.case ? caseHTML(d.case) : "") + "</div>" +
      '<button type="button" class="tb-btn" data-case>' + (d.case ? "↻ Rebuild bull / bear case" : (d.case_stale ? "✨ Update bull / bear case (new notes)" : "✨ Build my bull / bear case")) + "</button>" +
      '<details class="tb-timeline"><summary>Timeline · ' + d.total + " note" + (d.total === 1 ? "" : "s") + "</summary><ol>" +
      d.timeline.slice(0, 12).map(function (t) { return '<li class="' + esc(t.impact) + '"><time>' + esc(t.date) + "</time><b>" + esc(t.headline) + "</b>" + (t.key_change ? "<span>" + esc(t.key_change) + "</span>" : "") + "</li>"; }).join("") +
      '</ol><a href="/research-memory?ticker=' + encodeURIComponent(ticker) + '">Open all in Research Memory →</a></details></div>';
    var btn = box.querySelector("[data-case]");
    btn.addEventListener("click", function () {
      btn.disabled = true; btn.textContent = "Reading your notes…";
      getJSON("/api/memory/case/" + encodeURIComponent(ticker), { method: "POST" }).then(function (r) {
        btn.disabled = false;
        if (r && r.ok) { box.querySelector(".tb-case-box").innerHTML = caseHTML(r.case); btn.textContent = "↻ Rebuild bull / bear case"; }
        else btn.textContent = (r && r.error) || "Could not build the case";
      }).catch(function () { btn.disabled = false; btn.textContent = "Could not build the case"; });
    });
  }

  function init(el) {
    var t = (el.getAttribute("data-ticker-brief") || "").toUpperCase();
    if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(t) || el.dataset.ready) return;
    el.dataset.ready = "1";
    el.innerHTML = '<div class="tb-slot tb-slot-changed"></div><div class="tb-slot tb-slot-score"></div>';
    getJSON("/api/memory/changed/" + encodeURIComponent(t)).then(function (d) { renderChanged(el.querySelector(".tb-slot-changed"), d); }).catch(function () {});
    getJSON("/api/memory/scorecard/" + encodeURIComponent(t)).then(function (d) { renderScore(el.querySelector(".tb-slot-score"), d, t); }).catch(function () {});
  }
  function scan() { document.querySelectorAll("[data-ticker-brief]").forEach(init); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan); else scan();
  window.TSTickerBrief = { scan: scan };
})();
