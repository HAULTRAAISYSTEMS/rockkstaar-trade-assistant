/* Save any news story to Research Memory, with an AI summary pre-filled.
 *
 * Any element with data-save='{"ticker":..,"headline":..,"url":..}' becomes a
 * "Save to Memory" button. Clicking it opens a sheet, asks the server to
 * summarize the story, fills the fields, and saves the card on confirm.
 * Stories already in the notebook are marked "✓ In Memory".
 */
(function () {
  "use strict";
  if (window.TSMemory) return;

  var saved = { urls: {}, headlines: {} }, savedLoaded = false, loading = false, sheet = null, current = null, seq = 0;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function norm(s) { return String(s || "").toLowerCase().replace(/\s+/g, " ").trim(); }
  function readItem(btn) {
    try { return JSON.parse(btn.getAttribute("data-save") || "{}"); } catch (e) { return {}; }
  }
  function savedId(item) {
    return (item.url && saved.urls[item.url]) || saved.headlines[norm(item.headline)] || null;
  }
  function markButton(btn, id) {
    btn.classList.add("is-saved");
    btn.innerHTML = "✓ In Memory";
    btn.setAttribute("data-card", id);
    btn.title = "Open this note in Research Memory";
  }
  function markAll(root) {
    if (!savedLoaded) return;
    (root || document).querySelectorAll("[data-save]").forEach(function (btn) {
      var id = savedId(readItem(btn));
      if (id) markButton(btn, id);
    });
  }
  function loadSaved() {
    if (loading || !document.querySelector("[data-save]")) return;
    loading = true;
    fetch("/research-memory/api/saved", { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.ok) { saved = { urls: d.urls || {}, headlines: d.headlines || {} }; }
        savedLoaded = true; loading = false; markAll();
      }).catch(function () { savedLoaded = true; loading = false; });
  }

  function build() {
    sheet = document.createElement("div");
    sheet.className = "tsm-overlay";
    sheet.hidden = true;
    sheet.innerHTML =
      '<div class="tsm-sheet" role="dialog" aria-modal="true" aria-labelledby="tsm-title">' +
      '<header class="tsm-head"><div><span class="tsm-kicker">Save to Research Memory</span><h2 id="tsm-title"></h2>' +
      '<div class="tsm-meta"></div></div><button type="button" class="tsm-x" aria-label="Close">×</button></header>' +
      '<div class="tsm-status" aria-live="polite"></div>' +
      '<form class="tsm-form" novalidate>' +
      '<label>Summary<textarea name="summary" rows="3" maxlength="900"></textarea></label>' +
      '<label>Key points <small>one per line</small><textarea name="key_points" rows="4" maxlength="1600"></textarea></label>' +
      '<label class="tsm-req">Why it matters<textarea name="why_it_matters" rows="2" maxlength="4000" required></textarea></label>' +
      '<div class="tsm-row"><label>Key number or change<input name="key_change" maxlength="300"></label>' +
      '<label>Thesis impact<select name="thesis_impact"><option value="strengthened">Strengthened</option>' +
      '<option value="weakened">Weakened</option><option value="neutral">Neutral</option>' +
      '<option value="uncertain" selected>Uncertain</option></select></label></div>' +
      '<label>What would prove this wrong?<textarea name="disconfirming_evidence" rows="2" maxlength="3000"></textarea></label>' +
      '<label>Recall question<input name="review_question" maxlength="600"></label>' +
      '<label>My notes<textarea name="notes" rows="2" maxlength="6000" placeholder="Anything you want to add in your own words."></textarea></label>' +
      '<div class="tsm-actions"><button type="button" class="tsm-redo">↻ Re-summarize</button>' +
      '<button type="submit" class="tsm-save">Save to Memory</button></div>' +
      '<p class="tsm-foot">AI summaries can miss context. Check the key numbers against the source before you rely on them.</p>' +
      '</form></div>';
    document.body.appendChild(sheet);
    sheet.addEventListener("click", function (e) { if (e.target === sheet) close(); });
    sheet.querySelector(".tsm-x").addEventListener("click", close);
    sheet.querySelector(".tsm-redo").addEventListener("click", function () { if (current) summarize(current.item, true); });
    sheet.querySelector(".tsm-form").addEventListener("submit", function (e) { e.preventDefault(); save(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !sheet.hidden) close(); });
  }

  function field(name) { return sheet.querySelector('[name="' + name + '"]'); }
  function status(html, kind) {
    var el = sheet.querySelector(".tsm-status");
    el.className = "tsm-status" + (kind ? " is-" + kind : "");
    el.innerHTML = html || "";
  }
  function setBusy(busy) {
    sheet.querySelector(".tsm-form").classList.toggle("is-busy", !!busy);
    sheet.querySelector(".tsm-save").disabled = !!busy;
    sheet.querySelector(".tsm-redo").disabled = !!busy;
  }

  function fill(d) {
    field("summary").value = d.summary || "";
    field("key_points").value = (d.key_points || []).join("\n");
    field("why_it_matters").value = d.why_it_matters || "";
    field("key_change").value = d.key_change || "";
    field("thesis_impact").value = d.thesis_impact || "uncertain";
    field("disconfirming_evidence").value = d.disconfirming_evidence || "";
    field("review_question").value = d.review_question || "";
  }

  function summarize(item, fresh) {
    var my = ++seq;
    setBusy(true);
    status('<span class="tsm-spin"></span> Reading the story and pulling out what matters…', "busy");
    var body = JSON.stringify(Object.assign({}, item, fresh ? { fresh: true } : {}));
    fetch("/research-memory/api/summarize", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: body
    })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Unexpected reply." }; }); })
      .then(function (d) {
        if (my !== seq) return;
        setBusy(false);
        if (!d || !d.ok) { status(esc((d && d.error) || "Could not summarize this story. Fill it in yourself."), "warn"); return; }
        fill(d);
        var src = d.ai ? "✨ AI summary" : "Filled from the story text";
        var basis = d.basis ? " · from the " + esc(d.basis) : "";
        status(src + basis + (d.note ? '<br><small>' + esc(d.note) + "</small>" : "") +
               "<br><small>Review and edit before saving.</small>", d.ai ? "ok" : "warn");
        field("why_it_matters").focus({ preventScroll: true });
      })
      .catch(function () {
        if (my !== seq) return;
        setBusy(false);
        status("Could not reach the summarizer. You can still fill it in and save.", "warn");
      });
  }

  function open(btn) {
    if (!sheet) build();
    var item = readItem(btn);
    if (!item.headline) return;
    var existing = btn.getAttribute("data-card") || savedId(item);
    if (existing) { window.location.href = "/research-memory?saved=" + encodeURIComponent(existing) + "#card-" + encodeURIComponent(existing); return; }
    current = { btn: btn, item: item };
    sheet.querySelector(".tsm-form").reset();
    sheet.querySelector("#tsm-title").textContent = item.headline;
    sheet.querySelector(".tsm-meta").innerHTML =
      '<b>' + esc(item.ticker || "") + "</b>" + (item.source ? " · " + esc(item.source) : "") +
      (item.url && /^https?:\/\//.test(item.url) ? ' · <a href="' + esc(item.url) + '" target="_blank" rel="noopener noreferrer">Source ↗</a>' : "");
    sheet.hidden = false;
    document.documentElement.classList.add("tsm-lock");
    summarize(item, false);
  }

  function close() {
    if (!sheet) return;
    seq++;
    sheet.hidden = true;
    document.documentElement.classList.remove("tsm-lock");
    current = null;
  }

  function save() {
    if (!current) return;
    var why = field("why_it_matters").value.trim();
    if (!why) { status("Write why this matters before saving it.", "warn"); field("why_it_matters").focus(); return; }
    var item = current.item, btn = current.btn;
    var payload = {
      ticker: item.ticker, company_name: item.company_name || "", headline: item.headline,
      source: item.source, url: item.url, published_at: item.published_at,
      summary: field("summary").value.trim(),
      key_points: field("key_points").value.split(/\n+/).map(function (s) { return s.replace(/^[•\-\s]+/, "").trim(); }).filter(Boolean),
      why_it_matters: why, key_change: field("key_change").value.trim(),
      thesis_impact: field("thesis_impact").value,
      disconfirming_evidence: field("disconfirming_evidence").value.trim(),
      review_question: field("review_question").value.trim(),
      notes: field("notes").value.trim()
    };
    setBusy(true);
    status('<span class="tsm-spin"></span> Saving…', "busy");
    fetch("/research-memory/api/cards", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); })
      .then(function (d) {
        setBusy(false);
        if (!d || !d.ok) { status(esc((d && d.error) || "Could not save. Try again."), "warn"); return; }
        if (item.url) saved.urls[item.url] = d.id;
        saved.headlines[norm(item.headline)] = d.id;
        document.querySelectorAll("[data-save]").forEach(function (b) {
          var it = readItem(b);
          if ((item.url && it.url === item.url) || norm(it.headline) === norm(item.headline)) markButton(b, d.id);
        });
        markButton(btn, d.id);
        status((d.duplicate ? "Already in your notebook. " : "✓ Saved to Research Memory. ") +
               '<a href="' + esc(d.url) + '">Open notebook →</a>', "ok");
        setTimeout(function () { if (current && current.btn === btn) close(); }, 1600);
      })
      .catch(function () { setBusy(false); status("Could not save. Check your connection and try again.", "warn"); });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-save]");
    if (!btn) return;
    e.preventDefault();
    open(btn);
  });

  // News cards arrive after page load (ticker search, refresh, terminal).
  if (window.MutationObserver) {
    var pending = null;
    new MutationObserver(function () {
      if (pending) return;
      pending = setTimeout(function () {
        pending = null;
        if (!savedLoaded) loadSaved(); else markAll();
      }, 150);
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", loadSaved);
  else loadSaved();

  window.TSMemory = { open: open, mark: markAll, reload: loadSaved };
})();
