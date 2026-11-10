/* Stock page: tabs, live hero, price ladder "you are here" marker. */
(function () {
  "use strict";

  var TABS = ["levels", "news", "thesis", "fundamentals", "tools"];
  var STORE = "sd-tab";

  function $(id) { return document.getElementById(id); }
  function money(v) { return "$" + Number(v).toFixed(2); }

  // ── Tabs ──────────────────────────────────────────────────────────────────
  function showTab(name, opts) {
    if (TABS.indexOf(name) < 0) name = "levels";
    document.querySelectorAll(".sd-tabs [role=tab]").forEach(function (b) {
      var on = b.getAttribute("data-tab") === name;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll(".sd-panel").forEach(function (p) {
      p.hidden = p.getAttribute("data-panel") !== name;
    });
    try { localStorage.setItem(STORE, name); } catch (e) { /* storage off */ }
    if (!opts || !opts.silent) {
      try { history.replaceState(null, "", "#" + name); } catch (e) { /* ignore */ }
    }
  }

  function stickyOffset(includeTabs) {
    var cs = getComputedStyle(document.documentElement);
    var off = (parseFloat(cs.getPropertyValue("--sd-top")) || 0) + (parseFloat(cs.getPropertyValue("--sd-hero-h")) || 0);
    var tabs = document.querySelector(".sd-tabs");
    if (includeTabs && tabs) off += tabs.getBoundingClientRect().height + 6;
    return off + 12;
  }

  function scrollToEl(el, includeTabs) {
    if (!el) return;
    window.scrollTo({ top: el.getBoundingClientRect().top + window.pageYOffset - stickyOffset(includeTabs), behavior: "smooth" });
  }

  function scrollToTabs() { scrollToEl(document.querySelector(".sd-tabs"), false); }

  // "Size this trade" carries the plan's entry (top of the zone) and stop;
  // anything the trader already typed is left alone.
  function prefillSizer(entry, stop) {
    var e = $("psEntry"), st = $("psStop");
    if (!e || !st || !(parseFloat(entry) > 0) || !(parseFloat(stop) > 0)) return;
    if (!e.value) e.value = Number(entry).toFixed(2);
    if (!st.value) st.value = Number(stop).toFixed(2);
    if (typeof window.psSetMode === "function" && $("psModeSwing")) window.psSetMode("swing");
    if (typeof window.psCalc === "function") window.psCalc();
  }

  function initTabs() {
    var nav = document.querySelector(".sd-tabs");
    if (!nav) return;
    nav.addEventListener("click", function (e) {
      var b = e.target.closest("[role=tab]");
      if (b) showTab(b.getAttribute("data-tab"));
    });
    nav.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      var cur = nav.querySelector("[aria-selected=true]");
      var i = TABS.indexOf(cur ? cur.getAttribute("data-tab") : "levels");
      i = (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
      showTab(TABS[i]);
      var next = nav.querySelector('[data-tab="' + TABS[i] + '"]');
      if (next) next.focus();
      e.preventDefault();
    });
    document.addEventListener("click", function (e) {
      var a = e.target.closest("[data-sd-goto]");
      if (!a) return;
      e.preventDefault();
      showTab(a.getAttribute("data-sd-goto"));
      prefillSizer(a.getAttribute("data-sd-entry"), a.getAttribute("data-sd-stop"));
      var target = a.getAttribute("data-sd-scroll");
      var el = target && $(target);
      if (el) {
        scrollToEl(el, true);
      } else {
        scrollToTabs();
      }
    });
    var start = (location.hash || "").replace("#", "");
    // Old deep links (#plan, #notes) land on the tab that now holds them.
    var legacy = { plan: "tools", notes: "thesis", "ps-panel": "tools", "profile-news": "news",
                   "company-overview-card": "fundamentals", "profile-analysis": "levels" };
    if (legacy[start]) {
      showTab(legacy[start], { silent: true });
      var el = $(start);
      if (el) setTimeout(function () { scrollToEl(el, true); }, 80);
      return;
    }
    if (TABS.indexOf(start) < 0) {
      try { start = localStorage.getItem(STORE) || "levels"; } catch (e) { start = "levels"; }
    }
    showTab(start, { silent: true });
  }

  // ── Ladder ────────────────────────────────────────────────────────────────
  function placeMarker(price) {
    var list = $("sd-ladder");
    if (!list || !(price > 0)) return;
    list.setAttribute("data-price", price);
    var you = list.querySelector(".sd-rung--you");
    if (!you) {
      you = document.createElement("li");
      you.className = "sd-rung sd-rung--you";
      you.setAttribute("aria-current", "true");
      you.innerHTML = '<span class="sd-rung__label">You are here</span><span class="sd-rung__px"></span><span class="sd-rung__dist"></span>';
    }
    you.querySelector(".sd-rung__px").textContent = money(price);
    var rungs = Array.prototype.filter.call(list.children, function (li) { return li !== you; });
    var before = null;
    rungs.forEach(function (li) {
      var lvl = parseFloat(li.getAttribute("data-level"));
      var top = parseFloat(li.getAttribute("data-top"));
      var dist = li.querySelector(".sd-rung__dist");
      var inside = top > 0 && price >= lvl && price <= top;
      li.classList.toggle("is-inside", inside);
      if (dist && lvl > 0) {
        var ref = inside ? price : (price < lvl ? lvl : (top > 0 ? top : lvl));
        var pct = (ref / price - 1) * 100;
        dist.textContent = inside ? "inside" : (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%";
      }
      var key = top > 0 ? top : lvl;
      if (before === null && key < price && !inside) before = li;
    });
    if (before) list.insertBefore(you, before); else list.appendChild(you);
  }

  // ── Live hero (called by the detail poll) ─────────────────────────────────
  window.sdApplyLive = function (s) {
    var q = s.live_quote || null;
    var price = q && q.price != null ? q.price : s.current_price;
    if (price != null && $("sd-price")) $("sd-price").textContent = money(price);
    if ($("sd-chg") && s.gap_display) {
      $("sd-chg").textContent = s.gap_display;
      $("sd-chg").className = "sd-hero__chg " + (s.gap_class || "");
    }
    if (s.session_label && $("sd-session")) $("sd-session").textContent = s.session_label;
    if (q) {
      if ($("sd-source")) $("sd-source").textContent = q.source || "Live";
      if ($("sd-asof")) $("sd-asof").textContent = q.as_of ? "· " + q.as_of : "";
      var ms = $("sd-mobile-stamp");
      if (ms) ms.lastChild.textContent = " " + (s.session_label || "Live") + " · " + (q.source || "Live") + (q.as_of ? " · " + q.as_of : "");
      var dot = document.querySelector(".sd-hero .sd-dot");
      if (dot) dot.classList.add("is-live");
    }
    var tp = s.trade_permission;
    if (tp && tp.permission && $("sd-why-perm")) {
      $("sd-why-perm").textContent = tp.permission;
      if ($("sd-why-text") && tp.reason) $("sd-why-text").textContent = tp.reason;
      var why = $("sd-why");
      if (why) why.className = "sd-hero__why " + (tp.css || "");
    }
    if (Array.isArray(s.watch) && s.watch.length && $("sd-watch")) {
      var ul = $("sd-watch");
      ul.innerHTML = "";
      s.watch.forEach(function (w) {
        var li = document.createElement("li");
        li.className = w.ok ? "is-ok" : "is-no";
        var mark = document.createElement("span");
        mark.setAttribute("aria-hidden", "true");
        mark.textContent = w.ok ? "✓" : "○";
        li.appendChild(mark);
        li.appendChild(document.createTextNode(w.text));
        ul.appendChild(li);
      });
    }
    if (price != null) placeMarker(Number(price));
  };

  // ── Market cap chip in the hero ───────────────────────────────────────────
  function mirrorCap() {
    var src = document.querySelector("[data-cap-value]");
    var chip = $("sd-mcap");
    if (!src || !chip) return;
    function copy() {
      var t = (src.textContent || "").trim();
      if (t && !/loading|unavailable/i.test(t)) {
        chip.textContent = t.replace(/\s+USD$/, "") + " mkt cap";
        chip.hidden = false;
      }
    }
    copy();
    new MutationObserver(copy).observe(src, { childList: true, characterData: true, subtree: true });
  }

  // ── Sticky offsets + compact hero ─────────────────────────────────────────
  function initSticky() {
    var hero = $("sd-hero");
    if (!hero) return;
    var root = document.documentElement;
    function measure() {
      var nav = document.querySelector(".navbar");
      var top = 0;
      if (nav) {
        var pos = getComputedStyle(nav).position;
        if (pos === "sticky" || pos === "fixed") top = nav.getBoundingClientRect().height;
      }
      root.style.setProperty("--sd-top", top + "px");
      var sticky = getComputedStyle(hero).position === "sticky";
      root.style.setProperty("--sd-hero-h", (sticky ? hero.getBoundingClientRect().height : 0) + "px");
    }
    var anchor = hero.offsetTop;
    var ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        var y = window.pageYOffset;
        var compact = hero.classList.contains("is-compact");
        // Hysteresis so the height change can't flip it back and forth.
        if (!compact && y > anchor + 140) { hero.classList.add("is-compact"); measure(); }
        else if (compact && y < anchor + 20) { hero.classList.remove("is-compact"); measure(); }
      });
    }
    measure();
    window.addEventListener("resize", function () { anchor = hero.offsetTop; measure(); });
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  function init() {
    initTabs();
    initSticky();
    var list = $("sd-ladder");
    if (list) placeMarker(parseFloat(list.getAttribute("data-price")));
    mirrorCap();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
