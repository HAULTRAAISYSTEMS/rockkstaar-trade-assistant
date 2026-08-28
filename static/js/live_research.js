(function () {
  "use strict";
  var config = window.LR_PUBLIC_CONFIG || {};
  var shell = document.querySelector(".lr-public-shell");
  var feed = document.getElementById("research-feed");
  var featuredFeed = document.getElementById("featured-feed");
  var feedMessage = document.getElementById("feed-message");
  var liveState = document.getElementById("live-state");
  var pollTimer = null, statusTimer = null, cursor = "", failures = 0;
  var priorityRank = {Critical: 0, High: 1, Medium: 2, Low: 3};
  config.alertPrefs = config.alertPrefs || {};

  function csrf() { var meta = document.querySelector('meta[name="csrf-token"]'); return meta ? meta.content : ""; }
  function api(url, method, data) {
    return fetch(url, {method: method, headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: data === undefined ? undefined : JSON.stringify(data)}).then(function (response) {
      return response.json().then(function (payload) { if (!response.ok) throw new Error(payload.error || "Request failed"); return payload; });
    });
  }
  function node(tag, className, text) { var value = document.createElement(tag); if (className) value.className = className; if (text !== undefined && text !== null) value.textContent = text; return value; }
  function add(parent, child) { parent.appendChild(child); return child; }
  function exactTime(post) { return post.published_at || post.updated_at || ""; }
  function sourceTime(post) { return post.source_published_at || exactTime(post); }
  function relativeTime(value) {
    var date = new Date(value), now = new Date();
    if (isNaN(date.getTime())) return "Time unavailable";
    var seconds = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 1000));
    if (seconds < 60) return "Now";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
    if (seconds < 172800) return "Yesterday";
    return date.toLocaleDateString(undefined, {month: "short", day: "numeric"});
  }
  function timeNode(value) { var time = node("time", "lr-relative-time", relativeTime(value)); time.dateTime = value || ""; time.dataset.exactTime = value || ""; time.title = value || "Timestamp unavailable"; return time; }
  function refreshTimes() { document.querySelectorAll(".lr-relative-time").forEach(function (time) { time.textContent = relativeTime(time.dataset.exactTime || time.dateTime); }); }
  function formatValue(value) { return value === null || value === undefined ? "" : String(value); }

  function metricsNode(metrics) {
    if (!metrics || !metrics.length) return null;
    var strip = node("div", "lr-public-metrics");
    metrics.forEach(function (metric) {
      var comparison = String(metric.comparison || "not_applicable").toLowerCase();
      var item = add(strip, node("div", "lr-public-metric metric-" + comparison.replace(/[^a-z_]/g, "")));
      add(item, node("b", "", metric.label || "Metric"));
      var values = add(item, node("span"));
      if (metric.actual_value !== null && metric.actual_value !== undefined) values.appendChild(document.createTextNode(formatValue(metric.actual_value) + (metric.unit || "")));
      if (metric.expected_value !== null && metric.expected_value !== undefined) { add(values, node("i", "", " vs ")); values.appendChild(document.createTextNode(formatValue(metric.expected_value) + (metric.unit || ""))); }
      if ((metric.expected_value === null || metric.expected_value === undefined) && metric.previous_value !== null && metric.previous_value !== undefined) { add(values, node("i", "", " prev ")); values.appendChild(document.createTextNode(formatValue(metric.previous_value) + (metric.unit || ""))); }
      add(item, node("em", "", comparison.replace(/_/g, " ")));
    });
    return strip;
  }

  function actionButton(kind, post) {
    if (kind === "save") { var save = node("button", "lr-save" + (post.saved ? " is-saved" : ""), post.saved ? "★ Saved" : "☆ Save"); save.type = "button"; save.dataset.saved = post.saved ? "1" : "0"; return save; }
    var on = !!(config.alertPrefs || {})[post.ticker], alert = node("button", "lr-alert" + (on ? " is-on" : ""), on ? "🔔 Alert on" : "🔕 Alert"); alert.type = "button"; alert.dataset.ticker = post.ticker; alert.dataset.enabled = on ? "1" : "0"; return alert;
  }

  function researchItem(post, featured) {
    var article = node("article", "lr-public-item priority-" + String(post.priority || "Medium").toLowerCase() + (featured ? " is-featured" : ""));
    article.dataset.postId = post.id; article.dataset.publishedAt = exactTime(post); article.dataset.priority = post.priority || "Medium"; article.dataset.ticker = post.ticker; article.dataset.watchlisted = post.watchlisted ? "1" : "0";
    var head = add(article, node("div", "lr-public-item-head")), identity = add(head, node("div", "lr-public-identity"));
    var ticker = add(identity, node("a", "lr-public-ticker", post.ticker)); ticker.href = String(config.stockUrlTemplate || "/stock/__TICKER__").replace("__TICKER__", encodeURIComponent(post.ticker)) ;
    add(identity, node("span", "lr-public-company", post.company_name));
    var badges = add(head, node("div", "lr-public-badges")); add(badges, node("span", "lr-catalyst", post.catalyst_type || "BREAKING")); add(badges, node("span", "lr-priority-badge", post.priority || "Medium")); add(badges, node("span", "lr-sentiment lr-" + String(post.sentiment || "Neutral").toLowerCase(), post.sentiment || "Neutral")); head.appendChild(timeNode(exactTime(post)));
    add(article, node("h2", "", post.headline)); var metrics = metricsNode(post.metrics); if (metrics) article.appendChild(metrics);
    if (!featured && post.research_notes) add(article, node("div", "lr-public-notes lr-detail-only", post.research_notes));
    if (!featured && post.tradestaar_take) { var take = add(article, node("div", "lr-public-take lr-detail-only")); add(take, node("b", "", "TRADESTAAR TAKE")); add(take, node("div", "", post.tradestaar_take)); add(take, node("small", "", "Research and AI-assisted analysis can be wrong. Verify source information before trading.")); }
    var footer = add(article, node("footer", "lr-public-footer")), source = add(footer, node("div", "lr-public-source"));
    add(source, node("span", "", post.source_name || "Tradestaar Research")); source.appendChild(document.createTextNode(" · ")); source.appendChild(timeNode(sourceTime(post)));
    if (["8-K", "10-Q", "10-K"].indexOf(post.catalyst_type) >= 0) { source.appendChild(document.createTextNode(" · ")); add(source, node("span", "", post.catalyst_type)); }
    if (post.source_url) { source.appendChild(document.createTextNode(" · ")); var link = add(source, node("a", "", "Original source ↗")); link.href = post.source_url; link.target = "_blank"; link.rel = "noopener noreferrer"; }
    var actions = add(footer, node("div", "lr-public-actions")); actions.appendChild(actionButton("save", post)); actions.appendChild(actionButton("alert", post));
    return article;
  }

  function compareItems(left, right) {
    var sort = shell.dataset.sort || "newest";
    if (sort === "priority") { var leftRank = priorityRank[left.dataset.priority], rightRank = priorityRank[right.dataset.priority]; var rank = (leftRank === undefined ? 9 : leftRank) - (rightRank === undefined ? 9 : rightRank); if (rank) return rank; }
    if (sort === "watchlist") { var watched = Number(right.dataset.watchlisted || 0) - Number(left.dataset.watchlisted || 0); if (watched) return watched; }
    return String(right.dataset.publishedAt || "").localeCompare(String(left.dataset.publishedAt || ""));
  }
  function sortContainer(container) { Array.from(container.querySelectorAll(".lr-public-item")).sort(compareItems).forEach(function (item) { container.appendChild(item); }); }
  function removeEmpty(container) { var empty = container.querySelector(".lr-public-state"); if (empty) empty.remove(); }
  function updateCounts() { var count = feed.querySelectorAll(".lr-public-item").length, featured = featuredFeed.querySelectorAll(".lr-public-item").length; document.getElementById("feed-count").textContent = count + (count === 1 ? " result" : " results"); document.getElementById("featured-count").textContent = featured + " live"; }
  function qualifiesFeatured(post) { return ["Critical", "High"].indexOf(post.priority) >= 0 || post.catalyst_type === "BREAKING"; }
  function insertPost(post) {
    if (feed.querySelector('[data-post-id="' + CSS.escape(String(post.id)) + '"]')) return false;
    removeEmpty(feed); feed.appendChild(researchItem(post, false)); sortContainer(feed);
    if (qualifiesFeatured(post) && !featuredFeed.querySelector('[data-post-id="' + CSS.escape(String(post.id)) + '"]')) { removeEmpty(featuredFeed); featuredFeed.appendChild(researchItem(post, true)); sortContainer(featuredFeed); }
    updateCounts(); refreshTimes(); return true;
  }
  function newestCursor() { var value = ""; document.querySelectorAll(".lr-public-feed .lr-public-item[data-published-at]").forEach(function (item) { if ((item.dataset.publishedAt || "") > value) value = item.dataset.publishedAt; }); return value; }
  function setMessage(text, kind) { if (!text) { feedMessage.hidden = true; feedMessage.textContent = ""; return; } feedMessage.hidden = false; feedMessage.className = "lr-public-message " + (kind || ""); feedMessage.textContent = text; }

  function refreshIncremental() {
    var query = new URLSearchParams(location.search); query.set("since", cursor || newestCursor()); query.delete("view");
    liveState.textContent = "● Checking";
    fetch("/api/live-research/updates?" + query.toString(), {headers: {Accept: "application/json"}}).then(function (response) { return response.json().then(function (payload) { if (!response.ok) throw new Error(payload.error || "Update failed"); return payload; }); }).then(function (payload) {
      failures = 0; if (payload.alert_prefs) Object.assign(config.alertPrefs, payload.alert_prefs); var inserted = 0; (payload.posts || []).forEach(function (post) { if (insertPost(post)) inserted += 1; }); if (payload.cursor) cursor = payload.cursor; else cursor = newestCursor();
      liveState.textContent = "● Live"; setMessage(inserted ? inserted + " new published update" + (inserted === 1 ? "" : "s") + " added." : "", inserted ? "is-success" : "");
    }).catch(function () { failures += 1; liveState.textContent = "● REST fallback"; if (failures > 1) setMessage("Live updates are temporarily unavailable. The feed will retry automatically.", "is-error"); });
  }
  function startPolling() { if (!pollTimer) pollTimer = setInterval(refreshIncremental, 15000); }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
  function startRealtime() {
    if (!feed) return; cursor = newestCursor(); refreshTimes(); startPolling();
    if (typeof window._wsConnect === "function") { window._wsConnect("/ws/live-research", function (message) { if (message && message.type === "research.published") refreshIncremental(); }); statusTimer = setInterval(function () { if (window._wsGetStatus && window._wsGetStatus() === "live") { stopPolling(); liveState.textContent = "● Live"; } else { startPolling(); liveState.textContent = "● REST fallback"; } }, 3000); }
  }

  function setView(view) { if (["compact", "detailed"].indexOf(view) < 0) view = "detailed"; shell.dataset.view = view; document.getElementById("feed-view-input").value = view; document.querySelectorAll(".lr-view-toggle button").forEach(function (button) { button.setAttribute("aria-pressed", button.dataset.view === view ? "true" : "false"); }); var url = new URL(location.href); url.searchParams.set("view", view); history.replaceState({}, "", url); try { localStorage.setItem("tradestaar-research-view", view); } catch (_error) {} }
  document.querySelectorAll(".lr-view-toggle button").forEach(function (button) { button.onclick = function () { setView(button.dataset.view); }; });
  var initialView = config.view; if (!new URLSearchParams(location.search).has("view")) { try { initialView = localStorage.getItem("tradestaar-research-view") || initialView; } catch (_error) {} } setView(initialView);

  document.addEventListener("click", function (event) {
    var save = event.target.closest(".lr-save");
    if (save) { var saved = save.dataset.saved !== "1", postId = save.closest(".lr-public-item").dataset.postId; api("/api/live-research/posts/" + encodeURIComponent(postId) + "/bookmark", "POST", {saved: saved}).then(function () { document.querySelectorAll('.lr-public-item[data-post-id="' + CSS.escape(postId) + '"] .lr-save').forEach(function (button) { button.dataset.saved = saved ? "1" : "0"; button.classList.toggle("is-saved", saved); button.textContent = saved ? "★ Saved" : "☆ Save"; }); }).catch(function (error) { setMessage(error.message, "is-error"); }); return; }
    var alert = event.target.closest(".lr-alert");
    if (alert) { var enabled = alert.dataset.enabled !== "1", ticker = alert.dataset.ticker; api("/api/live-research/alerts/" + encodeURIComponent(ticker), "PUT", {enabled: enabled}).then(function () { config.alertPrefs[ticker] = enabled; document.querySelectorAll('.lr-alert[data-ticker="' + CSS.escape(ticker) + '"]').forEach(function (button) { button.dataset.enabled = enabled ? "1" : "0"; button.classList.toggle("is-on", enabled); button.textContent = enabled ? "🔔 Alert on" : "🔕 Alert"; }); }).catch(function (error) { setMessage(error.message, "is-error"); }); }
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startRealtime); else startRealtime();
}());
