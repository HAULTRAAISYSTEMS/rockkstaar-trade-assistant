(function () {
  "use strict";
  var config = window.LR_CONFIG || {};
  var editor = document.getElementById("research-editor");
  var metricRows = document.getElementById("metric-rows");
  var editorStatus = document.getElementById("editor-status");
  var allPosts = (config.posts || []).concat(config.urgentPosts || []);
  var postsById = {};
  allPosts.forEach(function (post) { postsById[String(post.id)] = post; });

  function request(url, method, data) {
    return fetch(url, {
      method: method,
      headers: {"Content-Type": "application/json", "X-CSRFToken": config.csrfToken || ""},
      body: data === undefined ? undefined : JSON.stringify(data)
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "Request failed");
        return payload;
      });
    });
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>\"]/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;"}[character];
    });
  }

  function localDateTime(value) {
    if (!value) return "";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "";
    var offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
  }

  function relativeAge(value) {
    var time = new Date(value).getTime();
    if (!time) return "unknown age";
    var seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
    if (seconds < 60) return "just now";
    if (seconds < 3600) return Math.floor(seconds / 60) + " min ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + " hr ago";
    return Math.floor(seconds / 86400) + "d ago";
  }

  document.querySelectorAll("time.relative-age").forEach(function (node) {
    node.textContent = relativeAge(node.getAttribute("datetime"));
  });

  var lastReview = config.lastReviewAt ? new Date(config.lastReviewAt).getTime() : 0;
  document.querySelectorAll(".lr-triage-row").forEach(function (row) {
    var created = new Date(row.dataset.createdAt).getTime();
    var flag = row.querySelector(".lr-new-flag");
    if (flag && created && created > lastReview) flag.hidden = false;
  });

  function metricRow(metric) {
    metric = metric || {};
    var row = document.createElement("div");
    row.className = "lr-metric-editor";
    row.innerHTML = '<select class="mt">' + (config.metricTypes || []).map(function (value) {
      return '<option ' + (value === metric.metric_type ? "selected" : "") + '>' + escapeHtml(value) + "</option>";
    }).join("") + '</select><input class="ml" placeholder="Revenue" value="' + escapeHtml(metric.label) + '">' +
      '<input class="ma" type="number" step="any" placeholder="Actual" value="' + (metric.actual_value == null ? "" : metric.actual_value) + '">' +
      '<input class="me" type="number" step="any" placeholder="Expected" value="' + (metric.expected_value == null ? "" : metric.expected_value) + '">' +
      '<input class="mp" type="number" step="any" placeholder="Previous" value="' + (metric.previous_value == null ? "" : metric.previous_value) + '">' +
      '<input class="mu" placeholder="Unit" value="' + escapeHtml(metric.unit) + '"><select class="mc">' +
      (config.comparisons || []).map(function (value) { return '<option ' + (value === metric.comparison ? "selected" : "") + '>' + escapeHtml(value) + "</option>"; }).join("") +
      '</select><button type="button" class="rm" aria-label="Remove metric">×</button>';
    return row;
  }

  function editorData() {
    var data = {};
    ["ticker", "company_name", "headline", "research_notes", "category", "sentiment", "priority", "catalyst_type", "source_name", "source_url", "source_published_at", "tradestaar_take", "take_origin"].forEach(function (id) {
      data[id] = document.getElementById(id).value;
    });
    data.should_notify = document.getElementById("should_notify").checked;
    data.metrics = Array.from(metricRows.children).map(function (row, index) {
      return {metric_type: row.querySelector(".mt").value, label: row.querySelector(".ml").value, actual_value: row.querySelector(".ma").value, expected_value: row.querySelector(".me").value, previous_value: row.querySelector(".mp").value, unit: row.querySelector(".mu").value, comparison: row.querySelector(".mc").value, sort_order: index};
    });
    return data;
  }

  function resetEditor() {
    if (!editor) return;
    editor.reset();
    document.getElementById("post-id").value = "";
    document.getElementById("take_origin").value = "manual";
    metricRows.innerHTML = "";
    editorStatus.textContent = "";
  }

  function fillEditor(post) {
    resetEditor();
    document.getElementById("draft-editor").open = true;
    document.getElementById("post-id").value = post.id;
    ["ticker", "company_name", "headline", "research_notes", "category", "sentiment", "priority", "catalyst_type", "source_name", "source_url", "tradestaar_take", "take_origin"].forEach(function (id) {
      document.getElementById(id).value = post[id] || "";
    });
    document.getElementById("source_published_at").value = localDateTime(post.source_published_at);
    document.getElementById("should_notify").checked = !!post.should_notify;
    (post.metrics || []).forEach(function (metric) { metricRows.appendChild(metricRow(metric)); });
    document.getElementById("draft-editor").scrollIntoView({behavior: "smooth", block: "start"});
  }

  if (editor) {
    document.getElementById("add-metric").onclick = function () { metricRows.appendChild(metricRow()); };
    metricRows.onclick = function (event) { if (event.target.classList.contains("rm")) event.target.parentNode.remove(); };
    document.getElementById("preview-post").onclick = function () {
      var data = editorData();
      document.getElementById("research-preview").textContent = data.ticker + " — " + data.company_name + "\n" + data.priority + " • " + data.catalyst_type + " • " + data.sentiment + "\n\n" + data.headline + "\n\n" + data.research_notes + "\n\nTradestaar Take:\n" + data.tradestaar_take;
    };
    document.getElementById("reset-editor").onclick = resetEditor;
    document.getElementById("save-draft").onclick = function () {
      var id = document.getElementById("post-id").value;
      request(id ? "/api/admin/live-research/posts/" + id : "/api/admin/live-research/posts", id ? "PATCH" : "POST", editorData()).then(function () { location.reload(); }).catch(function (error) { editorStatus.textContent = error.message; });
    };
    document.getElementById("publish-post").onclick = function () {
      var id = document.getElementById("post-id").value;
      var data = editorData();
      var save = id ? request("/api/admin/live-research/posts/" + id, "PATCH", data) : request("/api/admin/live-research/posts", "POST", data);
      save.then(function (result) {
        var postId = id || (result.post && result.post.id);
        if (!postId) throw new Error("Draft was not created");
        return request("/api/admin/live-research/posts/" + postId + "/publish", "POST");
      }).then(function () { location.reload(); }).catch(function (error) { editorStatus.textContent = error.message; });
    };
  }

  document.addEventListener("click", function (event) {
    var row = event.target.closest(".lr-triage-row");
    if (!row) return;
    var id = row.dataset.postId;
    var post = postsById[id];
    if (event.target.classList.contains("approve-post")) request("/api/admin/live-research/posts/" + id + "/approve", "POST").then(function () { location.reload(); });
    if (event.target.classList.contains("reject-post") && confirm("Reject and retain this intelligence for deduplication?")) request("/api/admin/live-research/posts/" + id + "/reject", "POST").then(function () { location.reload(); });
    if (event.target.classList.contains("edit-post") && post) fillEditor(post);
    if (event.target.classList.contains("publish-existing") && confirm("Publish this approved draft to the public feed?")) request("/api/admin/live-research/posts/" + id + "/publish", "POST").then(function () { location.reload(); });
    if (event.target.classList.contains("delete-post") && confirm("Delete this draft?")) request("/api/admin/live-research/posts/" + id, "DELETE").then(function () { location.reload(); });
  });

  function selectedIds() {
    var ids = {};
    document.querySelectorAll(".select-post:checked").forEach(function (node) { ids[node.value] = true; });
    return Object.keys(ids).map(Number);
  }
  function updateSelection() { var node = document.getElementById("selection-count"); if (node) node.textContent = selectedIds().length + " selected"; }
  document.addEventListener("change", function (event) { if (event.target.classList.contains("select-post")) updateSelection(); });
  var selectAll = document.getElementById("select-all-posts");
  if (selectAll) selectAll.onchange = function () { document.querySelectorAll(".lr-admin-posts .select-post").forEach(function (node) { node.checked = selectAll.checked; }); updateSelection(); };
  document.querySelectorAll("[data-bulk-action]").forEach(function (button) {
    button.onclick = function () {
      var ids = selectedIds(), action = button.dataset.bulkAction;
      if (!ids.length) return alert("Select at least one item.");
      if (!confirm(action.charAt(0).toUpperCase() + action.slice(1) + " " + ids.length + " selected item(s)?")) return;
      request("/api/admin/live-research/bulk", "POST", {action: action, post_ids: ids}).then(function () { location.reload(); }).catch(function (error) { alert(error.message); });
    };
  });
  var markReviewed = document.getElementById("mark-reviewed");
  if (markReviewed) markReviewed.onclick = function () { request("/api/admin/live-research/review-session", "POST", {}).then(function () { document.querySelectorAll(".lr-new-flag").forEach(function (flag) { flag.hidden = true; }); }); };

  var searchQuery = document.getElementById("research-search-query"), searchButton = document.getElementById("research-search-run"), searchStatus = document.getElementById("research-search-status"), searchResults = document.getElementById("research-search-results");
  function renderSearch(payload) {
    searchStatus.textContent = payload.message || ((payload.ticker || "") + " " + (payload.company_name || ""));
    searchResults.innerHTML = "";
    (payload.results || []).forEach(function (result) {
      var article = document.createElement("article"), existing = result.existing, label = existing ? ("Already " + existing.status) : "Create draft";
      article.className = "lr-search-result";
      article.innerHTML = '<div><strong>' + escapeHtml(result.ticker) + '</strong> <span class="lr-catalyst">' + escapeHtml(result.catalyst_type) + "</span><div>" + escapeHtml(result.headline) + '</div><small>' + escapeHtml(result.source_name) + " · " + escapeHtml(result.published_at) + "</small><p>" + escapeHtml(result.summary) + '</p></div><div><a href="' + escapeHtml(result.source_url) + '" target="_blank" rel="noopener">Source</a> <button type="button" class="research-create" ' + (existing ? "disabled" : "") + ">" + escapeHtml(label) + "</button></div>";
      var create = article.querySelector(".research-create");
      if (!existing) create.onclick = function () { create.disabled = true; create.textContent = "Creating…"; request("/api/admin/live-research/research-search/draft", "POST", result).then(function (created) { create.textContent = created.status === "existing" ? "Already " + created.post.status : "Draft created"; setTimeout(function () { location.reload(); }, 400); }).catch(function (error) { create.disabled = false; create.textContent = "Create draft"; searchStatus.textContent = error.message; }); };
      searchResults.appendChild(article);
    });
  }
  if (searchButton) {
    searchButton.onclick = function () { var query = (searchQuery.value || "").trim(); if (!query) return; searchStatus.textContent = "Researching fresh sources…"; searchResults.innerHTML = ""; fetch("/api/admin/live-research/research-search?q=" + encodeURIComponent(query), {headers: {"X-CSRFToken": config.csrfToken || ""}}).then(function (response) { return response.json().then(function (payload) { if (!response.ok) throw new Error(payload.error || "Search failed"); return payload; }); }).then(renderSearch).catch(function (error) { searchStatus.textContent = error.message; }); };
    searchQuery.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); searchButton.click(); } });
  }
}());
