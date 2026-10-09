"use strict";

/* ----------------------------------------------------------------------------
 * Strategy criteria (defaults). Thresholds for percentages are in percent.
 * ------------------------------------------------------------------------- */
const DEFAULT_CRITERIA = [
  { id: "g1",  label: "Revenue growth (1Y, TTM)", op: "≥", value: 5,  unit: "%" },
  { id: "e5",  label: "Earnings growth (5Y)",     op: "≥", value: 50, unit: "%" },
  { id: "g5",  label: "Revenue growth (5Y)",      op: "≥", value: 50, unit: "%" },
  { id: "roe", label: "Return on equity",         op: "≥", value: 15, unit: "%" },
  { id: "de",  label: "Debt to equity",           op: "≤", value: 1,  unit: "×" },
  { id: "fcf", label: "Free cash flow (TTM)",     op: ">", value: 0,  unit: "$M" },
  { id: "peg", label: "PEG (5Y expected)",        op: "≤", value: 2,  unit: "×" },
];

const PASS = {
  g1:  (r, t) => r.g1 != null && r.g1 * 100 >= t,
  e5:  (r, t) => r.e5 != null && r.e5 * 100 >= t,
  g5:  (r, t) => r.g5 != null && r.g5 * 100 >= t,
  roe: (r, t) => r.roe != null && r.roe * 100 >= t,
  de:  (r, t) => r.de != null && r.de >= 0 && r.de <= t,
  fcf: (r, t) => r.fcfu != null ? r.fcfu > t * 1e6 : (t === 0 && r.fcf != null && r.fcf > 0),
  peg: (r, t) => r.peg != null && r.peg > 0 && r.peg <= t,
};

const BASIS_NOTE = {
  ttm: "Last 12 months vs the 12 months before",
  fy: "Latest fiscal year vs the previous fiscal year",
  mrq: "Estimate: most recent quarter vs the same quarter a year earlier",
  yahoo_q: "Estimate: most recent quarter vs the same quarter a year earlier (Yahoo Finance)",
  fy_old: "Estimate: latest fiscal year vs the previous one (no recent quarterly data)",
};

const MARKET_LABEL = { US: "US", EU: "EU", CZ: "CZ" };

/* ----------------------------------------------------------------------------
 * Columns
 * ------------------------------------------------------------------------- */
const COLUMNS = [
  { key: "s",   label: "Ticker",  left: true, sticky: true, sort: (r) => r.s },
  { key: "n",   label: "Company", left: true, sort: (r) => (r.n || "").toLowerCase() },
  { key: "sec", label: "Sector",  left: true, sort: (r) => r.sec || "" },
  { key: "mc",  label: "Market Cap", sort: (r) => r.mc },
  { key: "crit", label: "Criteria", sort: (r) => r._pass + (r.mc || 0) / 1e16 },
  { key: "g1",  label: "Revenue Growth (1Y, TTM)", crit: "g1", sort: (r) => r.g1 },
  { key: "e5",  label: "Earnings Growth (5Y)", crit: "e5", sort: (r) => r.e5 },
  { key: "g5",  label: "Revenue Growth (5Y)", crit: "g5", sort: (r) => r.g5 },
  { key: "roe", label: "ROE", crit: "roe", sort: (r) => r.roe },
  { key: "de",  label: "Debt to Equity", crit: "de", sort: (r) => r.de },
  { key: "fcf", label: "Free Cash Flow (TTM)", crit: "fcf", sort: (r) => r.fcfu },
  { key: "peg", label: "PEG (5Y Exp)", crit: "peg", sort: (r) => r.peg },
  { key: "pe",  label: "P/E (TTM)", sort: (r) => r.pe },
];

/* ----------------------------------------------------------------------------
 * State
 * ------------------------------------------------------------------------- */
const state = {
  rows: [],
  fx: {},
  generated: null,
  dbUrl: null,
  market: "ALL",
  search: "",
  sector: "",
  country: "",
  minCap: 0,
  critFilter: "all",
  currency: "USD",
  sortKey: "crit",
  sortDir: -1,
  page: 0,
  pageSize: 50,
  criteria: loadCriteria(),
  filtered: [],
};

const $ = (id) => document.getElementById(id);

function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
}
function load(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : JSON.parse(v);
  } catch (e) { return fallback; }
}

function loadCriteria() {
  const saved = load("gs-criteria", null) || {};
  return DEFAULT_CRITERIA.map((c) => {
    const s = saved[c.id] || {};
    return { ...c, enabled: s.enabled !== false, value: typeof s.value === "number" ? s.value : c.value };
  });
}
function saveCriteria() {
  const out = {};
  state.criteria.forEach((c) => { out[c.id] = { enabled: c.enabled, value: c.value }; });
  store("gs-criteria", out);
}

/* ----------------------------------------------------------------------------
 * Formatting
 * ------------------------------------------------------------------------- */
const pctFmt = (v) => {
  if (v == null) return "—";
  const p = v * 100;
  return (Math.abs(p) >= 1000 ? p.toFixed(0) : p.toFixed(1)) + "%";
};
const numFmt = (v, d = 2) => (v == null ? "—" : v.toFixed(d));

const moneyFormatters = {};
function moneyFmt(value, currency) {
  if (value == null || !isFinite(value)) return "—";
  let cur = currency || "USD";
  if (cur === "GBp" || cur === "GBX") { cur = "GBP"; value = value / 100; }
  const key = cur;
  if (!moneyFormatters[key]) {
    try {
      moneyFormatters[key] = new Intl.NumberFormat("en-US", {
        style: "currency", currency: cur, notation: "compact", maximumFractionDigits: 2,
      });
    } catch (e) {
      moneyFormatters[key] = { format: (v) => cur + " " + new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v) };
    }
  }
  return moneyFormatters[key].format(value);
}

// value in USD -> display currency
function fromUsd(usd, localCurrency) {
  if (usd == null) return { value: null, currency: state.currency };
  if (state.currency === "USD") return { value: usd, currency: "USD" };
  const cur = state.currency === "LOCAL" ? normCur(localCurrency) : state.currency;
  const rate = state.fx[cur];
  if (!rate) return { value: usd, currency: "USD" };
  return { value: usd / rate, currency: cur };
}
function normCur(c) { return c === "GBp" || c === "GBX" ? "GBP" : (c || "USD"); }

function fcfDisplay(r) {
  if (state.currency === "LOCAL" && r.fcf != null) return { value: r.fcf, currency: r.fcur };
  if (r.fcfu == null && r.fcf != null) return { value: r.fcf, currency: r.fcur };
  return fromUsd(r.fcfu, r.fcur);
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ----------------------------------------------------------------------------
 * Data
 * ------------------------------------------------------------------------- */
async function loadData() {
  try {
    const resp = await fetch("data/stocks.json", { cache: "no-cache" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const f = data.fields;
    state.rows = data.rows.map((arr) => {
      const r = {};
      for (let i = 0; i < f.length; i++) r[f[i]] = arr[i];
      r._search = (r.s + " " + (r.n || "")).toLowerCase();
      return r;
    });
    state.fx = data.fx || {};
    state.generated = data.generated;
    state.dbUrl = data.db_url;
  } catch (e) {
    console.error(e);
    state.rows = [];
  }
}

function scoreRows() {
  const active = state.criteria.filter((c) => c.enabled);
  state.activeCount = active.length;
  for (const r of state.rows) {
    let n = 0;
    const res = {};
    for (const c of active) {
      const ok = PASS[c.id](r, c.value);
      res[c.id] = ok;
      if (ok) n++;
    }
    r._res = res;
    r._pass = n;
  }
}

function applyFilters() {
  const q = state.search.trim().toLowerCase();
  const need = state.critFilter === "all" ? state.activeCount
    : state.critFilter === "0" ? 0 : Math.max(0, state.activeCount + Number(state.critFilter));
  const base = state.rows.filter((r) =>
    (!state.sector || r.sec === state.sector) &&
    (!state.country || r.c === state.country) &&
    (!state.minCap || (r.mc != null && r.mc >= state.minCap)) &&
    (!q || r._search.includes(q)) &&
    r._pass >= need);

  // Tab counts reflect all other filters.
  const counts = { ALL: base.length, US: 0, EU: 0, CZ: 0 };
  base.forEach((r) => { counts[r.m] = (counts[r.m] || 0) + 1; });
  document.querySelectorAll("#market-tabs button").forEach((b) => {
    b.querySelector(".count").textContent = (counts[b.dataset.market] || 0).toLocaleString("en-US");
  });

  state.filtered = state.market === "ALL" ? base : base.filter((r) => r.m === state.market);
  sortRows();
}

function sortRows() {
  const col = COLUMNS.find((c) => c.key === state.sortKey) || COLUMNS[4];
  const dir = state.sortDir;
  state.filtered.sort((a, b) => {
    const va = col.sort(a), vb = col.sort(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va < vb) return -dir;
    if (va > vb) return dir;
    return (b.mc || 0) - (a.mc || 0);
  });
}

/* ----------------------------------------------------------------------------
 * Rendering
 * ------------------------------------------------------------------------- */
const EXT_ICON = '<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M14 3h7v7h-2V6.41l-9.29 9.3-1.42-1.42L17.59 5H14V3zM5 5h6v2H7v10h10v-4h2v6H5V5z"/></svg>';

function renderHead() {
  $("grid-head").innerHTML = COLUMNS.map((c) => {
    const arrow = state.sortKey === c.key ? (state.sortDir > 0 ? "▲" : "▼") : "";
    const cls = [c.left ? "left" : "", c.sticky ? "sticky" : ""].join(" ");
    const sortState = state.sortKey === c.key ? (state.sortDir > 0 ? "ascending" : "descending") : "none";
    return `<th class="${cls}" data-key="${c.key}" aria-sort="${sortState}" scope="col">${esc(c.label)} <span class="arrow">${arrow}</span></th>`;
  }).join("");
}

function cellClass(r, critId) {
  if (!critId) return "";
  const c = state.criteria.find((x) => x.id === critId);
  if (!c || !c.enabled) return "";
  return r._res[critId] ? "pass" : "fail";
}

function growthCell(r, key, yearsKey, critId) {
  const v = r[key];
  if (v == null) return `<td class="na">—</td>`;
  const years = r[yearsKey];
  const approx = years != null && years < 5;
  const title = approx ? ` title="Only ${years} years of history so far: the yearly growth rate is extended to 5 years"` : "";
  return `<td class="${cellClass(r, critId)}"${title}>${approx ? '<span class="approx">≈</span>' : ""}${pctFmt(v)}</td>`;
}

function renderBody() {
  const start = state.page * state.pageSize;
  const pageRows = state.filtered.slice(start, start + state.pageSize);
  const html = pageRows.map((r) => {
    const mc = fromUsd(r.mc, r.cur);
    const fcf = fcfDisplay(r);
    const total = state.activeCount;
    const badge = total === 0 ? "low" : r._pass === total ? "full" : r._pass >= total - 1 ? "near" : "low";
    const basisApprox = r.g1b && r.g1b !== "ttm" && r.g1b !== "fy";
    const g1Title = r.g1b ? ` title="${esc(BASIS_NOTE[r.g1b] || "")}"` : "";
    const sub = [r.x, r.c].filter(Boolean).join(" · ");
    return `<tr>
      <td class="left sticky"><a class="ticker" href="https://finance.yahoo.com/quote/${encodeURIComponent(r.s)}" target="_blank" rel="noopener">${esc(r.s)}${EXT_ICON}</a></td>
      <td class="left company" title="${esc(r.n)}">${esc(r.n || r.s)}<span class="sub"><span class="market-chip">${MARKET_LABEL[r.m] || r.m}</span>${esc(sub)}</span></td>
      <td class="left sector" title="${esc(r.ind || "")}">${esc(r.sec || "—")}</td>
      <td>${moneyFmt(mc.value, mc.currency)}</td>
      <td><span class="badge ${badge}">${r._pass}/${total}</span></td>
      ${r.g1 == null ? '<td class="na">—</td>' : `<td class="${cellClass(r, "g1")}"${g1Title}>${pctFmt(r.g1)}${basisApprox ? '<span class="approx">*</span>' : ""}</td>`}
      ${growthCell(r, "e5", "e5y", "e5")}
      ${growthCell(r, "g5", "g5y", "g5")}
      <td class="${r.roe == null ? "na" : cellClass(r, "roe")}">${pctFmt(r.roe)}</td>
      <td class="${r.de == null ? "na" : cellClass(r, "de")}">${numFmt(r.de)}</td>
      <td class="${fcf.value == null ? "na" : cellClass(r, "fcf")}">${moneyFmt(fcf.value, fcf.currency)}</td>
      <td class="${r.peg == null ? "na" : cellClass(r, "peg")}">${numFmt(r.peg)}</td>
      <td class="${r.pe == null ? "na" : ""}">${numFmt(r.pe, 1)}</td>
    </tr>`;
  }).join("");
  $("grid-body").innerHTML = html;

  const total = state.filtered.length;
  $("holdings").textContent = total.toLocaleString("en-US");
  const empty = $("empty");
  if (!state.rows.length) {
    empty.hidden = false;
    empty.innerHTML = "No data yet. The weekly update fills the database. On the first run that takes about an hour.";
  } else if (!total) {
    empty.hidden = false;
    empty.textContent = "No stocks match these filters. Try “Criteria met: Any” or loosen the strategy criteria.";
  } else {
    empty.hidden = true;
  }
  const end = Math.min(start + state.pageSize, total);
  $("page-info").textContent = total ? `${(start + 1).toLocaleString("en-US")}–${end.toLocaleString("en-US")} of ${total.toLocaleString("en-US")}` : "";
  $("prev").disabled = state.page === 0;
  $("next").disabled = end >= total;
}

function renderCriteria() {
  $("criteria-list").innerHTML = state.criteria.map((c) => `
    <div class="crit">
      <label><input type="checkbox" data-id="${c.id}" ${c.enabled ? "checked" : ""}> ${esc(c.label)}</label>
      <span class="op">${c.op}</span>
      <input type="number" step="any" data-id="${c.id}" value="${c.value}" aria-label="${esc(c.label)} threshold">
      <span class="unit">${c.unit}</span>
    </div>`).join("");
}

function renderFilterOptions() {
  const fill = (el, values, allLabel) => {
    const cur = el.value;
    el.innerHTML = `<option value="">${allLabel}</option>` +
      values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    el.value = values.includes(cur) ? cur : "";
  };
  const uniq = (key) => [...new Set(state.rows.map((r) => r[key]).filter(Boolean))].sort();
  fill($("f-sector"), uniq("sec"), "All sectors");
  fill($("f-country"), uniq("c"), "All countries");
}

function refresh(resetPage = true) {
  if (resetPage) state.page = 0;
  applyFilters();
  renderHead();
  renderBody();
  writeHash();
}

/* ----------------------------------------------------------------------------
 * URL hash (shareable views)
 * ------------------------------------------------------------------------- */
function writeHash() {
  const p = new URLSearchParams();
  if (state.market !== "ALL") p.set("market", state.market);
  if (state.search) p.set("q", state.search);
  if (state.sector) p.set("sector", state.sector);
  if (state.country) p.set("country", state.country);
  if (state.minCap) p.set("cap", String(state.minCap));
  if (state.critFilter !== "all") p.set("crit", state.critFilter);
  const h = p.toString();
  history.replaceState(null, "", h ? "#" + h : location.pathname + location.search);
}

function readHash() {
  const raw = location.hash.slice(1);
  if (!raw || raw === "how") return;
  const p = new URLSearchParams(raw);
  if (["US", "EU", "CZ"].includes(p.get("market"))) state.market = p.get("market");
  state.search = p.get("q") || "";
  state.sector = p.get("sector") || "";
  state.country = p.get("country") || "";
  state.minCap = Number(p.get("cap")) || 0;
  if (p.has("crit")) state.critFilter = p.get("crit");
}

function syncControls() {
  $("f-search").value = state.search;
  $("f-sector").value = state.sector;
  $("f-country").value = state.country;
  const capOption = [...$("f-mcap").options].find((o) => Number(o.value) === state.minCap);
  $("f-mcap").value = capOption ? capOption.value : "0";
  $("f-criteria").value = state.critFilter;
  $("f-currency").value = state.currency;
  $("page-size").value = String(state.pageSize);
  document.querySelectorAll("#market-tabs button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.market === state.market));
  });
}

/* ----------------------------------------------------------------------------
 * CSV download of the current view
 * ------------------------------------------------------------------------- */
function downloadView() {
  const head = ["Ticker", "Company", "Market", "Exchange", "Country", "Sector", "Industry", "Market Cap (USD)",
    "Criteria met", "Revenue Growth 1Y %", "Earnings Growth 5Y %", "Revenue Growth 5Y %", "ROE %",
    "Debt to Equity", "Free Cash Flow TTM (USD)", "PEG 5Y", "P/E TTM"];
  const pct = (v) => (v == null ? "" : (v * 100).toFixed(2));
  const lines = [head];
  for (const r of state.filtered) {
    lines.push([r.s, r.n, r.m, r.x, r.c, r.sec, r.ind, r.mc, `${r._pass}/${state.activeCount}`, pct(r.g1), pct(r.e5),
      pct(r.g5), pct(r.roe), r.de, r.fcfu, r.peg, r.pe]);
  }
  const csv = lines.map((l) => l.map((v) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "good-stocks-" + (state.market === "ALL" ? "all" : state.market.toLowerCase()) + ".csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ----------------------------------------------------------------------------
 * Events
 * ------------------------------------------------------------------------- */
function bind() {
  document.querySelectorAll("#market-tabs button").forEach((b) => b.addEventListener("click", () => {
    state.market = b.dataset.market;
    syncControls();
    refresh();
  }));

  let t;
  $("f-search").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { state.search = e.target.value; refresh(); }, 150);
  });
  $("f-sector").addEventListener("change", (e) => { state.sector = e.target.value; refresh(); });
  $("f-country").addEventListener("change", (e) => { state.country = e.target.value; refresh(); });
  $("f-mcap").addEventListener("change", (e) => { state.minCap = Number(e.target.value); refresh(); });
  $("f-criteria").addEventListener("change", (e) => { state.critFilter = e.target.value; refresh(); });
  $("f-currency").addEventListener("change", (e) => {
    state.currency = e.target.value;
    store("gs-currency", state.currency);
    refresh(false);
  });
  $("page-size").addEventListener("change", (e) => {
    state.pageSize = Number(e.target.value);
    store("gs-page-size", state.pageSize);
    refresh();
  });
  $("prev").addEventListener("click", () => { state.page = Math.max(0, state.page - 1); renderBody(); $("table-wrap").scrollTop = 0; });
  $("next").addEventListener("click", () => { state.page += 1; renderBody(); $("table-wrap").scrollTop = 0; });

  $("grid-head").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th) return;
    const key = th.dataset.key;
    if (state.sortKey === key) state.sortDir = -state.sortDir;
    else {
      state.sortKey = key;
      state.sortDir = ["s", "n", "sec", "de", "peg", "pe"].includes(key) ? 1 : -1;
    }
    sortRows();
    state.page = 0;
    renderHead();
    renderBody();
  });

  $("criteria-toggle").addEventListener("click", () => {
    const panel = $("criteria-panel");
    panel.hidden = !panel.hidden;
    $("criteria-toggle").textContent = panel.hidden ? "Show Strategy Criteria" : "Hide Strategy Criteria";
    $("criteria-toggle").setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("criteria-list").addEventListener("change", (e) => {
    const c = state.criteria.find((x) => x.id === e.target.dataset.id);
    if (!c) return;
    if (e.target.type === "checkbox") c.enabled = e.target.checked;
    else if (e.target.value !== "" && isFinite(Number(e.target.value))) c.value = Number(e.target.value);
    saveCriteria();
    scoreRows();
    refresh();
  });
  $("criteria-reset").addEventListener("click", () => {
    state.criteria = DEFAULT_CRITERIA.map((c) => ({ ...c, enabled: true }));
    saveCriteria();
    renderCriteria();
    scoreRows();
    refresh();
  });

  const menu = $("download-menu");
  $("download-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    $("download-btn").setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("click", () => { menu.hidden = true; $("download-btn").setAttribute("aria-expanded", "false"); });
  $("dl-view").addEventListener("click", (e) => { e.preventDefault(); downloadView(); });

  $("theme-btn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("gs-theme", next); } catch (e) { /* ignore */ }
  });
}

/* ----------------------------------------------------------------------------
 * Init
 * ------------------------------------------------------------------------- */
(async function init() {
  state.currency = load("gs-currency", "USD");
  state.pageSize = load("gs-page-size", 50);
  readHash();
  bind();
  renderCriteria();
  renderHead();
  await loadData();

  if (state.generated) {
    const d = new Date(state.generated);
    $("updated").textContent = "Last update: " + d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) + ".";
  }
  const dbHref = state.dbUrl || "#";
  ["dl-db", "db-link"].forEach((id) => { $(id).href = dbHref; });
  if (state.dbUrl) $("repo-link").href = state.dbUrl.split("/releases/")[0];

  renderFilterOptions();
  scoreRows();
  syncControls();
  refresh(false);
})();
