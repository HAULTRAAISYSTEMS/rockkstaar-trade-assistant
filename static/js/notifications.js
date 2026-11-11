/* Notification inbox: bell + drawer on every page, panel on Today. */
(function () {
  "use strict";
  var API = "/api/notifications";
  var bell = document.getElementById("nx-bell");
  var badge = document.getElementById("nx-badge");
  var drawer = document.getElementById("nx-drawer");
  var panel = document.getElementById("nx-panel");
  if (!bell && !panel) return;

  var lastUnread = null;
  var state = { kind: "", items: [], more: false, loading: false, readAt: "" };
  var panelState = { kind: "" };

  function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ""; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function ago(iso) {
    var t = Date.parse(iso); if (!t) return "";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    if (s < 7 * 86400) return Math.floor(s / 86400) + "d ago";
    return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function fullTime(iso) {
    var t = Date.parse(iso); if (!t) return "";
    return new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET";
  }
  function safeUrl(u) { return /^\/(?!\/)/.test(u || "") || /^https:\/\//i.test(u || "") ? u : ""; }

  function row(n) {
    var url = safeUrl(n.url);
    var tag = url ? "a" : "div";
    return "<li class=\"nx-item sev-" + esc(n.severity) + (n.unread ? " is-unread" : "") + "\">" +
      "<" + tag + (url ? " href=\"" + esc(url) + "\"" + (/^https:/i.test(url) ? " target=\"_blank\" rel=\"noopener noreferrer\"" : "") : "") + " class=\"nx-item__link\">" +
      "<span class=\"nx-item__icon\" aria-hidden=\"true\">" + esc(n.icon) + "</span>" +
      "<span class=\"nx-item__main\">" +
        "<span class=\"nx-item__top\"><b class=\"nx-item__title\">" + esc(n.title) + "</b>" +
        "<time datetime=\"" + esc(n.created_at) + "\" title=\"" + esc(fullTime(n.created_at)) + "\">" + esc(ago(n.created_at)) + "</time></span>" +
        (n.body ? "<span class=\"nx-item__body\">" + esc(n.body) + "</span>" : "") +
        "<span class=\"nx-item__meta\">" + esc(n.kind_label) + (n.ticker ? " · " + esc(n.ticker) : "") +
        (n.unread ? "<i class=\"nx-new\">New</i>" : "") + "</span>" +
      "</span></" + tag + "></li>";
  }

  function empty(kind) {
    return "<li class=\"nx-empty\"><b>All quiet" + (kind ? " here" : "") + ".</b>" +
      "<span>Setups, position warnings, insider buys, thesis changes, earnings and site status show up here the moment the app raises them.</span></li>";
  }

  function setBadge(n) {
    if (!badge) return;
    n = Math.max(0, n | 0);
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.hidden = n === 0;
    if (bell) bell.setAttribute("aria-label", n ? "Notifications, " + n + " unread" : "Notifications");
    if (lastUnread !== null && n > lastUnread && bell) {
      bell.classList.remove("nx-ring"); void bell.offsetWidth; bell.classList.add("nx-ring");
    }
    lastUnread = n;
  }

  function pollUnread() {
    if (document.hidden) return;
    fetch(API + "/unread", { cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) setBadge(d.unread); })
      .catch(function () {});
  }

  function get(kind, before, limit) {
    var q = "?limit=" + (limit || 40) + (kind ? "&kind=" + encodeURIComponent(kind) : "") + (before ? "&before=" + encodeURIComponent(before) : "");
    return fetch(API + q, { cache: "no-store", headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status); return r.json();
    });
  }

  function chips(host, kinds, active, onPick) {
    if (!host || host.getAttribute("data-built")) return;
    host.setAttribute("data-built", "1");
    var all = [{ kind: "", label: "All" }].concat(kinds || []);
    host.innerHTML = all.map(function (k) {
      return "<button type=\"button\" class=\"nx-chip\" data-kind=\"" + esc(k.kind) + "\" aria-pressed=\"" + (k.kind === active) + "\">" +
        (k.icon ? "<span aria-hidden=\"true\">" + esc(k.icon) + "</span> " : "") + esc(k.label) + "</button>";
    }).join("");
    host.addEventListener("click", function (e) {
      var b = e.target.closest(".nx-chip"); if (!b) return;
      host.querySelectorAll(".nx-chip").forEach(function (c) { c.setAttribute("aria-pressed", c === b ? "true" : "false"); });
      onPick(b.getAttribute("data-kind") || "");
    });
  }

  // ── Drawer ──────────────────────────────────────────────────────────────
  var list = document.getElementById("nx-list");
  var more = document.getElementById("nx-more");
  var status = document.getElementById("nx-status");

  function loadDrawer(append) {
    if (state.loading || !list) return;
    state.loading = true;
    if (!append) { list.innerHTML = "<li class=\"nx-empty\"><span>Loading…</span></li>"; }
    var before = append && state.items.length ? state.items[state.items.length - 1].created_at : null;
    get(state.kind, before, 40).then(function (d) {
      chips(document.getElementById("nx-chips"), d.kinds, state.kind, function (k) { state.kind = k; loadDrawer(false); });
      state.items = append ? state.items.concat(d.items) : d.items;
      state.more = d.more;
      list.innerHTML = state.items.length ? state.items.map(row).join("") : empty(state.kind);
      if (more) more.hidden = !d.more;
      if (status) status.textContent = d.unread ? d.unread + " new" : "You're caught up";
    }).catch(function () {
      list.innerHTML = "<li class=\"nx-empty\"><b>Couldn't load notifications.</b><span>Check your connection and try again.</span></li>";
    }).then(function () { state.loading = false; });
  }

  function markRead() {
    fetch(API + "/read", { method: "POST", headers: { "X-CSRFToken": csrf(), Accept: "application/json" } })
      .then(function (r) { if (r.ok) setBadge(0); }).catch(function () {});
  }

  function openDrawer() {
    if (!drawer) return;
    if (typeof drawer.showModal === "function") drawer.showModal(); else drawer.setAttribute("open", "");
    document.documentElement.classList.add("nx-open");
    loadDrawer(false);
    // Opening the inbox counts as reading it; "New" tags stay until it's closed.
    setTimeout(markRead, 800);
  }
  function closeDrawer() {
    if (!drawer) return;
    if (typeof drawer.close === "function" && drawer.open) drawer.close(); else drawer.removeAttribute("open");
    document.documentElement.classList.remove("nx-open");
  }

  if (bell) bell.addEventListener("click", function (e) { e.preventDefault(); openDrawer(); });
  if (drawer) {
    drawer.addEventListener("click", function (e) {
      if (e.target === drawer || e.target.closest("[data-nx-close]")) closeDrawer();
      else if (e.target.closest(".nx-item__link")) closeDrawer();
    });
    drawer.addEventListener("close", function () { document.documentElement.classList.remove("nx-open"); refreshPanel(); });
    var readBtn = document.getElementById("nx-read");
    if (readBtn) readBtn.addEventListener("click", function () { markRead(); list.querySelectorAll(".is-unread").forEach(function (li) { li.classList.remove("is-unread"); var n = li.querySelector(".nx-new"); if (n) n.remove(); }); if (status) status.textContent = "You're caught up"; });
    if (more) more.addEventListener("click", function () { loadDrawer(true); });
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-nx-open]")) { e.preventDefault(); openDrawer(); }
  });

  // ── Today panel ─────────────────────────────────────────────────────────
  function refreshPanel() {
    if (!panel) return;
    var host = document.getElementById("nx-panel-list");
    get(panelState.kind, null, 5).then(function (d) {
      chips(document.getElementById("nx-panel-chips"), d.kinds, panelState.kind, function (k) { panelState.kind = k; refreshPanel(); });
      host.innerHTML = d.items.length ? d.items.map(row).join("") : empty(panelState.kind);
      var c = document.getElementById("nx-panel-count");
      if (c) { c.textContent = d.unread ? d.unread + " new" : "Caught up"; c.classList.toggle("has-new", !!d.unread); }
      setBadge(d.unread);
    }).catch(function () {
      host.innerHTML = "<li class=\"nx-empty\"><b>Couldn't load notifications.</b><span>They'll appear on the next refresh.</span></li>";
    });
  }

  pollUnread();
  setInterval(pollUnread, 60000);
  if (panel) { refreshPanel(); setInterval(function () { if (!document.hidden) refreshPanel(); }, 60000); }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) pollUnread(); });
  // Keep relative times fresh in an open drawer.
  setInterval(function () {
    document.querySelectorAll(".nx-item time[datetime]").forEach(function (t) { t.textContent = ago(t.getAttribute("datetime")); });
  }, 60000);
})();
