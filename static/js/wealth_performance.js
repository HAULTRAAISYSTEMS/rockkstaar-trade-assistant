(() => {
  "use strict";
  const root = document.getElementById("performance-center");
  if (!root) return;

  const get = id => document.getElementById("pc-" + id);
  const account = get("account");
  const strategy = get("strategy");
  const status = get("status");
  const chart = get("chart");
  const chartRange = get("range");
  const current = new URLSearchParams(location.search);
  if (Array.from(strategy.options).some(option => option.value === current.get("strategy"))) {
    strategy.value = current.get("strategy");
  }

  const dash = "—";
  const usd = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
  const finite = Number.isFinite;
  const number = (value, digits = 2) => finite(value) ? value.toFixed(digits) : dash;
  const money = value => finite(value) ? usd.format(value) : dash;
  const signed = (value, suffix) => finite(value) ? (value > 0 ? "+" : "") + number(value) + suffix : dash;
  const pct = value => signed(value, "%");
  const pp = value => signed(value, "");
  const time = value => {
    if (!value) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  };
  const strategyNames = {long_term: "Long-Term Investing", active: "Active Trading", unclassified: "Unclassified"};
  const allocationColors = ["#26f596", "#12aafa", "#ad82ed", "#8caabc"];
  const seriesInfo = [
    {key: "portfolio", label: "Portfolio", color: "#29f894"},
    {key: "benchmark", label: "S&P 500 TR", color: "#26aaff"},
    {key: "target", label: "Target", color: "#bc83ff", dash: [6, 4]}
  ];
  let response;
  let inflight;
  let plot;
  let selectedDay = null;
  let chartFrame = 0;
  let syncMessage = "";

  function color(element, value) {
    element.classList.toggle("pc-up", finite(value) && value > 0);
    element.classList.toggle("pc-down", finite(value) && value < 0);
  }

  function field(name, value, tone) {
    for (const element of root.querySelectorAll('[data-field="' + name + '"]')) {
      element.textContent = value;
      color(element, tone);
    }
  }

  function tableRow(parent, label, values) {
    const row = parent.insertRow();
    const heading = document.createElement("th");
    heading.scope = "row";
    heading.textContent = label;
    row.append(heading);
    for (const [content, tone] of values) {
      const cell = row.insertCell();
      cell.textContent = content;
      color(cell, tone);
    }
    return row;
  }

  function emptyTable(parent, columns, message) {
    const cell = parent.insertRow().insertCell();
    cell.colSpan = columns;
    cell.textContent = message;
  }

  function replaceList(container, textItems) {
    container.replaceChildren();
    for (const item of textItems) {
      const li = document.createElement("li");
      li.textContent = item;
      container.append(li);
    }
  }

  function notice(message, retry = false, ledger = true) {
    get("notice").hidden = !message;
    get("notice-message").textContent = message;
    get("ledger-link").hidden = !ledger;
    const ledgerUrl = new URL(get("ledger-link").href);
    ledgerUrl.search = account.value === "all" ? "" : "?p=" + account.value;
    get("ledger-link").href = ledgerUrl;
    root.querySelector('[data-action="retry"]').hidden = !retry;
  }

  function renderPortfolio(data) {
    const p = data.position;
    for (const key of ["value", "unrealized", "realized", "dividends_ytd", "fees_ytd", "investment_profit",
      "basis", "cash", "available_cash", "deposits", "withdrawals", "dividends", "interest", "fees"]) {
      const value = key === "fees_ytd" && finite(p[key]) ? -p[key] : p[key];
      const tone = ["unrealized", "realized", "fees_ytd", "investment_profit"].includes(key) ? value : null;
      field(key, money(value), tone);
    }
    field("unrealized-pct", pct(p.unrealized_pct), p.unrealized_pct);
    field("quote-summary", data.sync.live ? "Schwab balances" : (data.sync.stored ? "Schwab snapshot" : ""));
    field("realized-period", finite(p.realized) ? "Recorded history" : "");
    field("fee-period", finite(p.fees_ytd) ? "Trading and account fees" : "");
    field("interest-sub", finite(p.interest) ? money(p.interest) + " interest in recorded history" : "");

    const holdings = p.holdings.filter(row => finite(row.return_pct)).sort((a, b) => b.return_pct - a.return_pct);
    for (const [key, value] of [["best", holdings[0]], ["worst", holdings.at(-1)]]) {
      field(key, value ? value.ticker + " " + pct(value.return_pct) : dash, value?.return_pct);
    }
    field("largest", percentUnsigned(p.concentration?.largest_pct));
    field("top5", percentUnsigned(p.concentration?.top5_pct));
    field("effective", number(p.concentration?.effective_holdings));

    const allocation = [];
    for (const [key, label] of Object.entries(strategyNames)) {
      const s = data.strategies[key] || {};
      field(key + "-return_pct", pct(s.return_pct), s.return_pct);
      for (const fieldName of ["value", "unrealized", "realized", "dividends_ytd"]) {
        field(key + "-" + fieldName, money(s[fieldName]), fieldName === "value" ? null : s[fieldName]);
      }
      if (finite(s.value) && finite(s.cash)) allocation.push([label, s.value - s.cash, s.return_pct]);
    }
    allocation.push(["Cash & Sweep", p.cash, null]);
    const list = get("allocation");
    list.replaceChildren();
    const bands = [];
    let position = 0;
    for (const [label, value, returns] of allocation) {
      const li = document.createElement("li");
      const labelElement = document.createElement("span");
      const swatch = document.createElement("i");
      const shade = label === "Cash & Sweep" ? allocationColors[3] : allocationColors[Object.values(strategyNames).indexOf(label)];
      swatch.style.backgroundColor = shade;
      labelElement.append(swatch, label);
      const amount = document.createElement("strong");
      amount.textContent = money(value);
      const change = document.createElement("span");
      change.textContent = pct(returns);
      color(change, returns);
      li.append(labelElement, amount, change);
      list.append(li);
      if (p.value > 0 && value >= 0) {
        const next = Math.min(100, position + value / p.value * 100);
        if (next > position) bands.push(shade + " " + position + "% " + next + "%");
        position = next;
      }
    }
    const validAllocation = allocation.every(([, value]) => finite(value) && value >= 0)
      && Math.abs(allocation.reduce((sum, [, value]) => sum + value, 0) - p.value) < .05;
    get("donut").style.background = bands.length && validAllocation
      ? "conic-gradient(" + [...bands, "#1e3e56 " + position + "% 100%"].join(",") + ")" : "";
  }

  function percentUnsigned(value) {
    return finite(value) ? number(value) + "%" : dash;
  }

  function renderReturns(history) {
    const periods = get("periods");
    periods.replaceChildren();
    const data = history.periods || ["Today", "1 Week", "1 Month", "3 Months", "YTD", "1 Year", "All-Time"]
      .map(label => ({label}));
    for (const p of data) {
      const label = p.label === "Today" && p.end && p.end !== response.position.as_of ? "Latest session" : p.label;
      const row = tableRow(periods, label, [[pct(p.portfolio), p.portfolio], [pct(p.benchmark)], [pp(p.excess), p.excess]]);
      if (p.start) row.title = p.start + " to " + p.end;
    }
    const r = history.risk || {};
    for (const [key, value] of Object.entries({twr: history.twr_pct, xirr: history.mwr_annual_pct,
      annual: history.twr_annual_pct, drawdown: r.max_drawdown?.pct, volatility: r.volatility_pct, alpha: r.alpha_pct})) {
      field(key, key === "volatility" ? percentUnsigned(value) : pct(value), key === "volatility" ? null : value);
    }
    for (const key of ["sharpe", "sortino", "calmar", "beta"]) field(key, number(r[key]));

    const ytd = data.find(p => p.label === "YTD");
    field("comparison-portfolio", pct(ytd?.portfolio), ytd?.portfolio);
    field("comparison-benchmark", pct(ytd?.benchmark));
    field("comparison-excess", pp(ytd?.excess), ytd?.excess);
    field("comparison-sharpe", number(r.aligned_portfolio?.sharpe) + " / " + number(r.benchmark?.sharpe));

    const heat = get("heatmap");
    heat.replaceChildren();
    const years = Object.entries(history.heatmap || {}).reverse();
    if (!years.length) emptyTable(heat, 14, "No monthly returns available.");
    for (const [year, monthly] of years) {
      const values = Array.from({length: 12}, (_, index) => monthly[index + 1]).concat(history.years[year]);
      const row = tableRow(heat, year, values.map(value => [finite(value) ? signed(value, "%").replace(/(\.\d)\d%$/, "$1%") : dash]));
      values.forEach((value, index) => {
        const cell = row.cells[index + 1];
        cell.dataset.tone = !finite(value) || value === 0 ? "neutral" : (value > 0 ? "up" : "down");
        if (finite(value)) {
          cell.style.setProperty("--intensity", String(Math.min(.9, .28 + Math.abs(value) / 20)));
          cell.title = pct(value);
        }
      });
    }

    const attr = get("attribution");
    attr.replaceChildren();
    let rows = history.attribution || [];
    if (!rows.length) emptyTable(attr, 4, "No attribution available.");
    if (rows.length > 6) rows = [...rows.slice(0, 4), ...rows.slice(-2)];
    const scale = Math.max(1, ...rows.map(row => Math.abs(row.contribution_pct)));
    for (const row of rows) {
      const tr = tableRow(attr, row.ticker, [[pp(row.contribution_pct), row.contribution_pct],
        [money(row.profit), row.profit], [percentUnsigned(row.weight_pct)]]);
      const bar = document.createElement("span");
      bar.className = "pc-attribution-bar" + (row.contribution_pct < 0 ? " pc-negative" : "");
      bar.setAttribute("aria-hidden", "true");
      const fill = document.createElement("span");
      fill.style.width = Math.abs(row.contribution_pct) / scale * 100 + "%";
      bar.append(fill);
      tr.cells[1].prepend(bar);
    }
    chartRange.disabled = !history.ok;
    get("chart-empty").hidden = Boolean(history.ok);
    get("chart-empty").textContent = history.ok ? "" : "Historical values unavailable";
    setPlot();
  }

  function setProgress(progress, value) {
    progress.hidden = !finite(value);
    if (finite(value)) progress.value = Math.max(0, Math.min(100, value));
  }

  function renderGoals(data) {
    const goal = data.selected_goal;
    field("goal-rate", percentUnsigned(goal.return_pct));
    field("goal-amount", money(goal.contribution));
    field("net", money(goal.net));
    const contribution = finite(goal.net) ? goal.net / goal.contribution * 100 : null;
    field("contribution-sub", "of " + money(goal.contribution));
    field("progress-contribution", percentUnsigned(contribution));
    field("remaining", finite(goal.remaining) ? money(goal.remaining) + " remaining · " + data.days_left + " days left" : "Contribution history unavailable");
    setProgress(get("contribution-progress"), contribution);
    const actual = data.history.periods?.find(period => period.label === "YTD")?.portfolio;
    const trajectory = data.history.growth_chart?.target.at(-1);
    const delta = finite(actual) && finite(trajectory) ? actual - trajectory : null;
    field("progress-return", finite(actual) ? pct(actual) + " / " + percentUnsigned(goal.return_pct) : dash, actual);
    field("target-gap", finite(delta) ? pp(delta) + " pp vs. trajectory" : "Return history unavailable", delta);
    const returnProgress = finite(actual)
      ? (goal.return_pct > 0 ? actual / goal.return_pct * 100 : (actual >= goal.return_pct ? 100 : 0))
      : null;
    setProgress(get("return-progress"), returnProgress);
    const history = get("goal-history");
    history.replaceChildren();
    for (const year of data.goals) {
      tableRow(history, year.year, [[pct(year.actual_return) + " / " + percentUnsigned(year.return_pct)],
        [money(year.net) + " / " + money(year.contribution)]]);
    }
  }

  function renderSources(data) {
    const p = data.position;
    const sources = [];
    if (data.history.ok) sources.push("Ledger and Yahoo Finance daily closing prices · " + data.history.start + " to " + data.history.end);
    if (data.benchmark.as_of) sources.push(data.benchmark.label + " (" + data.benchmark.symbol + ") · " + data.benchmark.source + " · " + data.benchmark.as_of);
    else sources.push("S&P 500 total-return series unavailable.");
    for (const item of p.quote_sources) sources.push(item.source + (item.as_of ? " · " + time(item.as_of) : " · timestamp unavailable"));
    for (const asOf of data.sync.as_of) sources.push("Schwab account observation · " + time(asOf));
    replaceList(get("sources"), [...new Set(sources)]);
    replaceList(get("warnings"), [
      ...data.warnings,
      ...(p.unpriced.length ? ["Current price unavailable for " + p.unpriced.join(", ") + "."] : []),
      ...(p.stale.length ? ["Last stored price is stale for " + p.stale.join(", ") + "."] : [])
    ]);
    const coverage = data.history.ok ? "History through " + data.history.end : "History incomplete";
    const sync = data.sync.live ? "Schwab connected" : (data.sync.stored ? "Schwab snapshot" : "No Schwab balance in this view");
    status.textContent = syncMessage || sync + " · " + coverage;
    notice(data.history.ok ? "" : data.history.reason);
  }

  function refreshOptions(data) {
    for (const p of data.accounts) {
      if (!Array.from(account.options).some(option => option.value === String(p.id))) {
        account.add(new Option(p.name, p.id));
      }
    }
    root.querySelector('[data-action="goals"]').disabled = false;
    root.querySelector('[data-action="accounts"]').disabled = !data.accounts.length;
  }

  async function load() {
    inflight?.abort();
    const request = new AbortController();
    inflight = request;
    status.textContent = "Loading portfolio performance…";
    root.setAttribute("aria-busy", "true");
    const params = new URLSearchParams();
    if (account.value !== "all") params.set("p", account.value);
    if (strategy.value !== "all") params.set("strategy", strategy.value);
    const query = params.toString() ? "?" + params : "";
    history.replaceState(null, "", location.pathname + query);
    try {
      const r = await fetch(root.dataset.api + query, {signal: request.signal, cache: "no-store"});
      if (!r.ok) throw new Error("Performance data is temporarily unavailable.");
      response = await r.json();
      renderPortfolio(response);
      renderReturns(response.history);
      renderGoals(response);
      refreshOptions(response);
      renderSources(response);
    } catch (error) {
      if (error.name === "AbortError") return;
      status.textContent = error.message;
      notice(error.message, true, false);
    } finally {
      if (inflight === request) root.removeAttribute("aria-busy");
    }
  }

  function setPlot() {
    const history = response?.history;
    const source = history?.chart;
    const path = chartRange.value === "all" && source ? {
      days: source.days,
      portfolio: source.growth.map(value => (value - 1) * 100),
      benchmark: (source.spy || []).map(value => finite(value) ? (value - 1) * 100 : null)
    } : history?.growth_chart;
    const lines = path ? seriesInfo.filter(line => path[line.key]?.some(finite)) : [];
    root.querySelectorAll(".pc-legend li").forEach((item, index) =>
      item.classList.toggle("is-missing", !lines.includes(seriesInfo[index])));
    selectedDay = null;
    plot = path ? {path, lines} : null;
    drawPlot();
  }

  function drawPlot() {
    const width = chart.clientWidth;
    const height = chart.clientHeight;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    chart.width = width * ratio;
    chart.height = height * ratio;
    const ctx = chart.getContext("2d");
    if (!ctx || !plot || width < 40 || height < 40) {
      get("chart-readout").textContent = "";
      return;
    }
    const {path, lines} = plot;
    const last = path.days.length - 1;
    const values = lines.flatMap(line => path[line.key].filter(finite));
    const min = Math.min(0, ...values), max = Math.max(0, ...values);
    const span = max - min || 1;
    const lo = min - span * .12, hi = max + span * .14;
    const left = 49, right = width - 14, top = 9, bottom = height - 21;
    const stamps = path.days.map(day => Date.parse(day));
    const timeSpan = stamps[last] - stamps[0];
    const x = index => left + (right - left) * (timeSpan ? (stamps[index] - stamps[0]) / timeSpan : 0);
    const y = value => bottom - (bottom - top) * (value - lo) / (hi - lo);
    plot.x = x;

    ctx.scale(ratio, ratio);
    ctx.lineWidth = 1;
    ctx.font = "11px IBM Plex Sans, sans-serif";
    ctx.fillStyle = "#afcbe1";
    for (let i = 0; i <= 4; i++) {
      const value = lo + (hi - lo) * i / 4;
      const py = y(value);
      ctx.beginPath();
      ctx.strokeStyle = "#1f3b50";
      ctx.moveTo(left, py);
      ctx.lineTo(right, py);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(number(value, span < 7 ? 1 : 0) + "%", left - 5, py + 4);
    }
    const ticks = Math.min(last, width < 420 ? 3 : 6);
    for (let i = 0; i <= ticks; i++) {
      const index = Math.round(last * i / Math.max(ticks, 1));
      const px = x(index);
      ctx.beginPath();
      ctx.moveTo(px, top);
      ctx.lineTo(px, bottom);
      ctx.stroke();
      ctx.textAlign = i === 0 ? "left" : (i === ticks ? "right" : "center");
      const day = path.days[index];
      const stamp = new Date(day + "T12:00:00Z");
      ctx.fillText(new Intl.DateTimeFormat("en-US", {month: "short",
        ...(timeSpan > 31536000000 ? {year: "2-digit"} : {day: "numeric"}), timeZone: "UTC"}).format(stamp), px, height - 6);
    }

    const portfolioValues = path.portfolio;
    if (portfolioValues?.every(finite) && last > 0) {
      const fill = ctx.createLinearGradient(0, top, 0, bottom);
      fill.addColorStop(0, "#19fa9866");
      fill.addColorStop(1, "#09a6e303");
      ctx.beginPath();
      ctx.moveTo(x(0), bottom);
      portfolioValues.forEach((value, index) => ctx.lineTo(x(index), y(value)));
      ctx.lineTo(x(last), bottom);
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
    }
    for (const line of lines) {
      ctx.lineWidth = line.dash ? 1.5 : 2;
      ctx.strokeStyle = line.color;
      ctx.setLineDash(line.dash || []);
      ctx.beginPath();
      let started = false;
      path[line.key].forEach((value, index) => {
        if (!finite(value)) {
          started = false;
          return;
        }
        if (started) ctx.lineTo(x(index), y(value));
        else ctx.moveTo(x(index), y(value));
        started = true;
      });
      ctx.stroke();
    }
    ctx.setLineDash([]);
    const i = selectedDay ?? last;
    if (selectedDay !== null) {
      ctx.strokeStyle = "#b2d7f4";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x(i), top);
      ctx.lineTo(x(i), bottom);
      ctx.stroke();
    }
    const valuesAtDate = lines.map(line => line.label + " " + pct(path[line.key][i]));
    const text = [path.days[i], ...valuesAtDate].join("  ·  ");
    if (get("chart-readout").textContent !== text) get("chart-readout").textContent = text;
  }

  function requestPlot() {
    if (chartFrame) return;
    chartFrame = requestAnimationFrame(() => {
      chartFrame = 0;
      drawPlot();
    });
  }

  chart.addEventListener("pointermove", event => {
    if (!plot?.x) return;
    const pos = event.clientX - chart.getBoundingClientRect().left;
    let lo = 0, hi = plot.path.days.length - 1;
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (plot.x(mid) <= pos) lo = mid;
      else hi = mid;
    }
    selectedDay = Math.abs(plot.x(lo) - pos) < Math.abs(plot.x(hi) - pos) ? lo : hi;
    requestPlot();
  });
  chart.addEventListener("pointerleave", () => { selectedDay = null; requestPlot(); });
  chart.addEventListener("keydown", event => {
    if (!plot || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const last = plot.path.days.length - 1;
    const i = selectedDay ?? last;
    selectedDay = {Home: 0, End: last, ArrowLeft: Math.max(i - 1, 0), ArrowRight: Math.min(i + 1, last)}[event.key];
    requestPlot();
  });
  chartRange.addEventListener("change", setPlot);
  new ResizeObserver(requestPlot).observe(chart);

  const goalForm = get("goals-form");
  const accountForm = get("accounts-form");
  const goalYear = goalForm.elements.year;
  const settingsAccount = accountForm.elements.account;

  function showGoal() {
    const goal = response.goals.find(row => row.year === Number(goalYear.value));
    goalForm.elements.return_pct.value = goal.return_pct;
    goalForm.elements.contribution.value = goal.contribution;
  }

  function showAccount() {
    const a = response.accounts.find(row => row.id === Number(settingsAccount.value));
    accountForm.elements.strategy.value = a.strategy;
    accountForm.elements.history_confirmed.checked = a.history_confirmed;
  }

  goalYear.addEventListener("change", showGoal);
  settingsAccount.addEventListener("change", showAccount);
  root.querySelectorAll("[data-close]").forEach(button =>
    button.addEventListener("click", () => button.closest("dialog").close()));

  root.querySelector('[data-action="goals"]').addEventListener("click", () => {
    goalYear.replaceChildren(...response.goals.map(goal => new Option(goal.year, goal.year)));
    goalYear.value = response.selected_goal.year;
    showGoal();
    goalForm.querySelector('[role="status"]').textContent = "";
    get("goals-dialog").showModal();
  });
  root.querySelector('[data-action="accounts"]').addEventListener("click", () => {
    settingsAccount.replaceChildren(...response.accounts.map(a => new Option(a.name, a.id)));
    if (account.value !== "all") settingsAccount.value = account.value;
    showAccount();
    accountForm.querySelector('[role="status"]').textContent = "";
    get("accounts-dialog").showModal();
  });

  async function save(form, path, body) {
    const message = form.querySelector('[role="status"]');
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    message.textContent = "Saving…";
    try {
      const r = await fetch(root.dataset.api + path, {method: "PUT",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (!r.ok) {
        const result = await r.json().catch(() => ({}));
        throw new Error(result.error || "Settings could not be saved.");
      }
      form.closest("dialog").close();
      syncMessage = "";
      await load();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  goalForm.addEventListener("submit", event => {
    event.preventDefault();
    save(goalForm, "/goals/" + goalYear.value, {
      return_pct: goalForm.elements.return_pct.valueAsNumber,
      contribution: goalForm.elements.contribution.valueAsNumber
    });
  });
  accountForm.addEventListener("submit", event => {
    event.preventDefault();
    save(accountForm, "/accounts/" + settingsAccount.value, {
      strategy: accountForm.elements.strategy.value,
      history_confirmed: accountForm.elements.history_confirmed.checked
    });
  });
  root.querySelector('[data-action="sync"]').addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    status.textContent = "Synchronizing Schwab balances…";
    try {
      const r = await fetch(root.dataset.sync, {method: "POST", cache: "no-store"});
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error("Schwab sync unavailable. Check your connection.");
      syncMessage = "Schwab sync complete";
      await load();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  for (const filter of [account, strategy]) filter.addEventListener("change", () => { syncMessage = ""; load(); });
  let balancePending = false;
  async function refreshBalance() {
    if (document.hidden || !response || balancePending || root.hasAttribute("aria-busy")) return;
    const selection = response;
    const query = new URLSearchParams(location.search);
    query.set("view", "balance");
    balancePending = true;
    try {
      const refreshed = await fetch(root.dataset.api + "?" + query, {cache: "no-store"});
      if (!refreshed.ok) return;
      const update = await refreshed.json();
      if (response !== selection || root.hasAttribute("aria-busy")) return;
      Object.assign(response.position, update.position);
      const p = response.position;
      p.investment_profit = finite(p.value) && finite(p.capital_flows_to_date)
        ? p.value - p.capital_flows_to_date : null;
      response.sync = update.sync;
      for (const [name, balances] of Object.entries(update.strategies)) {
        Object.assign(response.strategies[name] || {}, balances);
      }
      renderPortfolio(response);
      renderSources(response);
    } catch {
      status.textContent += " · Latest balance refresh unavailable";
    } finally {
      balancePending = false;
    }
  }

  root.querySelector('[data-action="retry"]').addEventListener("click", load);
  setInterval(refreshBalance, 60000);
  document.addEventListener("visibilitychange", refreshBalance);
  load();
})();
