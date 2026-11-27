(() => {
  "use strict";
  const panel = document.getElementById("pc-history-import");
  if (!panel) return;
  const root = panel.closest("#performance-center");
  const get = id => document.getElementById("pc-" + id);
  const select = get("import-account");
  const brokerForm = get("broker-history-form");
  const csvForm = get("csv-history-form");
  const status = get("import-status");
  const commit = get("import-commit");
  const currency = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
  const quantity = new Intl.NumberFormat("en-US", {maximumFractionDigits: 6});
  const displayMoney = value => Number.isFinite(value) ? currency.format(value) : "—";
  const displayQuantity = value => Number.isFinite(value) ? quantity.format(value) : "—";
  const source = {schwab_api: "Schwab sync", schwab_csv: "Schwab CSV", csv: "CSV", manual: "Manual"};
  let accounts = [];
  let dashboardSelection;
  let busy = false;
  let preview;
  let recentRequest;

  function endpoint(pid) {
    return panel.dataset.history.replace(/\/0$/, "/" + pid);
  }

  async function json(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || "Transaction history is temporarily unavailable.");
    return data;
  }

  function row(parent, values) {
    const tr = parent.insertRow();
    for (const value of values) {
      const cell = tr.insertCell();
      if (value instanceof HTMLElement) cell.append(value);
      else cell.textContent = value;
    }
  }

  function cash(row) {
    if (row.type === "OPTION_BUY" || row.type === "OPTION_SELL") {
      const direction = row.type === "OPTION_BUY" ? -1 : 1;
      return row.amount ?? direction * row.shares * row.price * 100 - (row.fees || 0);
    }
    if (row.type === "BUY") return -(row.shares * row.price + (row.fees || 0));
    if (row.type === "SELL") return row.shares * row.price - (row.fees || 0);
    if (["WITHDRAWAL", "FEE", "TAX"].includes(row.type)) return -row.amount;
    if (["DEPOSIT", "DIVIDEND", "INTEREST"].includes(row.type)) return row.amount;
    return null;
  }

  function controls() {
    const account = accounts.find(item => String(item.id) === select.value);
    select.disabled = busy || !accounts.length;
    brokerForm.querySelector("button").disabled = busy || !account?.linked;
    csvForm.querySelector("button").disabled = busy || !account;
    get("csv-file").disabled = busy || !account;
    commit.disabled = busy || !preview || !get("import-rows").querySelector("input[type=checkbox]:checked");
  }

  function clearPreview() {
    preview = null;
    get("import-preview").hidden = true;
    get("import-rows").replaceChildren();
    controls();
  }

  async function recent(open = false) {
    recentRequest?.abort();
    const pid = select.value;
    const section = get("recent-history");
    section.hidden = !pid;
    if (!pid) return;
    if (open && !section.open) {
      section.open = true;
      return;
    }
    if (!section.open) return;
    const request = new AbortController();
    recentRequest = request;
    const message = get("recent-status");
    const table = get("recent-rows");
    table.replaceChildren();
    message.textContent = "Loading ledger transactions…";
    try {
      const data = await json(endpoint(pid), {signal: request.signal, cache: "no-store"});
      if (request.signal.aborted || select.value !== pid) return;
      for (const item of data.transactions) {
        row(table, [item.date, item.type, item.ticker || "—", displayQuantity(item.shares),
          displayMoney(item.price), displayMoney(cash(item)), source[item.source] || "Ledger"]);
      }
      message.textContent = data.transactions.length ? "Latest " + data.transactions.length + " ledger transactions."
        : "This ledger has no transactions yet.";
    } catch (error) {
      if (error.name !== "AbortError") message.textContent = error.message;
    }
  }

  function selectionChanged() {
    clearPreview();
    csvForm.reset();
    status.textContent = select.value ? "Preview the selected account’s Schwab history or upload its CSV."
      : "Select the ledger account for this history.";
    recent();
  }

  root.addEventListener("performance:accounts", event => {
    const {accounts: list, selected, today} = event.detail;
    accounts = list;
    const before = select.value;
    const desired = !busy && selected !== dashboardSelection && selected !== "all" ? selected : before;
    dashboardSelection = selected;
    select.replaceChildren(new Option("Select an account", ""),
      ...accounts.map(account => new Option(account.label || account.name, account.id)));
    select.value = accounts.some(account => String(account.id) === desired) ? desired : "";
    for (const input of brokerForm.querySelectorAll("input[type=date]")) input.max = today;
    if (before !== select.value) selectionChanged();
    controls();
  });
  root.addEventListener("performance:import-account", event => {
    const pid = String(event.detail);
    if (!busy && select.value !== pid && accounts.some(account => String(account.id) === pid)) {
      select.value = pid;
      selectionChanged();
    }
  });
  select.addEventListener("change", selectionChanged);
  get("recent-history").addEventListener("toggle", () => {
    if (get("recent-history").open) recent();
  });

  function renderPreview(data, kind, pid) {
    preview = {data, kind, pid};
    const rows = get("import-rows");
    rows.closest(".pc-scroll").hidden = false;
    commit.hidden = false;
    data.transactions.forEach((item, index) => {
      const pick = document.createElement("input");
      pick.type = "checkbox";
      pick.checked = !item.needs_cost;
      pick.dataset.index = index;
      pick.setAttribute("aria-label", "Include transaction " + (index + 1));
      let cost = displayMoney(item.price);
      if (item.needs_cost) {
        cost = document.createElement("input");
        cost.type = "number";
        cost.min = "0";
        cost.step = "any";
        cost.dataset.cost = index;
        cost.placeholder = "Cost per share";
        cost.setAttribute("aria-label", "Cost per share for transaction " + (index + 1));
      }
      const type = {OPTION_BUY: "Buy to open", OPTION_SELL: "Sell to close"}[item.type] || item.type;
      row(rows, [pick, item.date, type, item.ticker || "—", displayQuantity(item.shares), cost,
        displayMoney(cash(item)), displayMoney(item.fees)]);
    });
    const reasonCounts = new Map();
    for (const item of data.skipped) {
      const totals = reasonCounts.get(item.reason) || {count: 0, cash: 0, cashCount: 0};
      totals.count++;
      if (Number.isFinite(item.cash)) {
        totals.cash += item.cash;
        totals.cashCount++;
      }
      reasonCounts.set(item.reason, totals);
    }
    get("import-reasons").replaceChildren(...[...reasonCounts.entries()].sort((a, b) => b[1].count - a[1].count)
      .map(([reason, totals]) => {
        const li = document.createElement("li");
        li.textContent = totals.count + " × " + reason
          + (totals.cashCount ? " · CSV cash for " + totals.cashCount + " rows: " + displayMoney(totals.cash) : "");
        return li;
      }));
    const warnings = get("import-warnings");
    warnings.replaceChildren(...data.skipped.map(item => {
      const li = document.createElement("li");
      li.textContent = "Row " + item.row + (item.date ? " · " + item.date : "") + ": " + item.reason;
      return li;
    }));
    get("import-skipped").hidden = !data.skipped.length;
    get("import-skipped-count").textContent = data.skipped.length + " rows need review";
    get("import-preview").hidden = false;
    const ready = data.transactions.length;
    const costs = data.transactions.some(item => item.needs_cost);
    status.textContent = ready + " eligible rows for " + select.selectedOptions[0].textContent
      + (data.start ? " · " + data.start + " through " + data.end : "") + "."
      + (ready ? " Review the rows, then choose Import selected into ledger to save them." : "")
      + (costs ? " Provide the cost basis before selecting a share transfer." : "");
  }

  async function runPreview(event, kind) {
    event.preventDefault();
    if (busy || !select.value) return;
    const pid = select.value;
    const values = new FormData(event.currentTarget);
    clearPreview();
    busy = true;
    controls();
    status.textContent = kind === "broker" ? "Reading Schwab transaction history…" : "Reading CSV…";
    try {
      const data = kind === "broker"
        ? await json(endpoint(pid) + "/preview", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify(Object.fromEntries(values))})
        : await json(panel.dataset.csvPreview, {method: "POST", body: values});
      if (select.value === pid) renderPreview(data, kind, pid);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      controls();
    }
  }

  brokerForm.addEventListener("submit", event => runPreview(event, "broker"));
  csvForm.addEventListener("submit", event => runPreview(event, "csv"));
  get("import-rows").addEventListener("change", controls);

  commit.addEventListener("click", async () => {
    if (!preview || busy || select.value !== preview.pid) return;
    const {data, kind, pid} = preview;
    const indexes = [...get("import-rows").querySelectorAll("input[type=checkbox]:checked")].map(input => Number(input.dataset.index));
    if (!indexes.length) return;
    let body = {token: data.token, indexes};
    let url = endpoint(pid) + "/commit";
    if (kind === "csv") {
      const transactions = [];
      for (const index of indexes) {
        const item = {...data.transactions[index]};
        const cost = get("import-rows").querySelector('[data-cost="' + index + '"]');
        if (cost) {
          if (!Number.isFinite(cost.valueAsNumber) || !cost.checkValidity()) {
            status.textContent = "Enter a cost per share for each selected share transfer.";
            cost.focus();
            return;
          }
          item.price = cost.valueAsNumber;
        }
        transactions.push(item);
      }
      body = {portfolio_id: pid, transactions};
      url = panel.dataset.csvCommit;
    }
    busy = true;
    controls();
    status.textContent = "Importing selected activity into the ledger…";
    try {
      const result = await json(url, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
      });
      clearPreview();
      status.textContent = "Imported " + result.added + " · Already recorded or matching " + result.duplicates
        + (result.rejected?.length ? " · Rejected " + result.rejected.length : "")
        + (data.skipped.length ? " · " + data.skipped.length + " preview rows still need review" : "") + ".";
      // Keep skipped rows visible even after saving the eligible transactions.
      if (data.skipped.length) {
        get("import-preview").hidden = false;
        get("import-rows").closest(".pc-scroll").hidden = true;
        commit.hidden = true;
      }
      recent(true);
      root.dispatchEvent(new Event("performance:ledger-updated"));
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      controls();
    }
  });
})();
