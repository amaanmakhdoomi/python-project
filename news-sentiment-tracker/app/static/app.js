/* Election Pulse dashboard */

const state = {
  status: null,
  historyDays: 30,
  stance: { A: "all", B: "all" },
  chart: null,
  refreshing: false,
  lastLoaded: 0,
};

const $ = (id) => document.getElementById(id);

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));

async function getJSON(url, options = {}) {
  const response = await fetch(url, { headers: { Accept: "application/json" }, ...options });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {
      /* ignore */
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

function timeAgo(iso) {
  if (!iso) return "unknown";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const diff = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(diff);
  const units = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, seconds] of units) {
    if (abs >= seconds) return formatter.format(Math.round(diff / seconds), unit);
  }
  return "just now";
}

function shortDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const sameDay = date.toDateString() === new Date().toDateString();
  return date.toLocaleString(undefined, sameDay
    ? { hour: "2-digit", minute: "2-digit" }
    : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function signed(value, digits = 2) {
  const number = Number(value) || 0;
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}`;
}

let toastTimer = null;
function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.remove("opacity-0", "translate-y-3");
  element.classList.add("opacity-100", "translate-y-0");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    element.classList.add("opacity-0", "translate-y-3");
    element.classList.remove("opacity-100", "translate-y-0");
  }, 3800);
}

function stanceWord(score) {
  if (score >= 0.05) return "Favorable";
  if (score <= -0.05) return "Unfavorable";
  return "Neutral";
}

function stanceClasses(score) {
  if (score >= 0.05) return "border-emerald-400/25 bg-emerald-400/10 text-emerald-300";
  if (score <= -0.05) return "border-rose-400/25 bg-rose-400/10 text-rose-300";
  return "border-slate-500/30 bg-slate-500/10 text-slate-300";
}

function impactClasses(impact) {
  if (impact === "high") return "text-amber-300";
  if (impact === "medium") return "text-sky-300";
  return "text-slate-500";
}

function setText(id, value) {
  const element = $(id);
  if (element) element.textContent = value;
}

function renderStatus(data) {
  state.status = data;
  const { scores, counts, drivers } = data;

  setText("partyAName", data.party_a);
  setText("partyBName", data.party_b);
  setText("feedAName", data.party_a);
  setText("feedBName", data.party_b);
  setText("driversAName", data.party_a);
  setText("driversBName", data.party_b);
  setText("disclaimer", data.disclaimer);
  setText("windowLabel", `${data.window_days} days`);
  setText("articleCount", counts.analyzed.toLocaleString());

  const pA = Number(scores.p_a) || 50;
  const pB = Number(scores.p_b) || 50;

  setText("partyAPct", `${pA.toFixed(1)}%`);
  setText("partyBPct", `${pB.toFixed(1)}%`);
  setText("partyAScore", signed(scores.s_a));
  setText("partyBScore", signed(scores.s_b));
  setText("partyAStance", scores.has_data ? stanceWord(scores.s_a) : "No data");
  setText("partyBStance", scores.has_data ? stanceWord(scores.s_b) : "No data");

  $("barA").style.width = `${pA}%`;
  $("barB").style.width = `${pB}%`;
  setText("barALabel", `${data.party_a} ${pA.toFixed(1)}%`);
  setText("barBLabel", `${data.party_b} ${pB.toFixed(1)}%`);
  setText("deltaLabel", `Δ ${signed(scores.delta)}`);

  const run = data.last_run;
  setText("updatedAt", shortDateTime((run && run.finished_at) || scores.generated_at));
  const dot = document.querySelector("#lastRunPill .ticker-dot");
  const label = $("lastRunText");
  if (data.scheduler && data.scheduler.running) {
    dot.className = "ticker-dot text-amber-400";
    label.textContent = "Update in progress…";
  } else if (run && run.status === "error") {
    dot.className = "ticker-dot text-rose-400";
    label.textContent = "Last update failed";
  } else if (run && run.finished_at) {
    dot.className = "ticker-dot text-emerald-400";
    label.textContent = `Updated ${timeAgo(run.finished_at)}`;
  } else if (scores.has_data) {
    dot.className = "ticker-dot text-emerald-400";
    label.textContent = `Snapshot ${timeAgo(scores.generated_at)}`;
  } else {
    dot.className = "ticker-dot text-slate-400";
    label.textContent = "Awaiting first update";
  }

  setText(
    "footerMeta",
    `Sentiment engine: ${data.llm === "none" ? "local lexicon" : data.llm}` +
      ` · feeds: ${counts.feeds} · auto-update every ${data.scheduler.interval_minutes} min`
  );
  setText("feedMeta", `${counts.articles.toLocaleString()} articles collected · ${counts.analyzed.toLocaleString()} analyzed`);

  renderDrivers("A", drivers.A || []);
  renderDrivers("B", drivers.B || []);
}

function renderDrivers(party, items) {
  const list = $(`drivers${party}`);
  if (!items.length) {
    list.innerHTML = `<li class="rounded-xl border border-dashed border-slate-700/60 bg-slate-900/30 px-3 py-3 text-xs text-slate-500">
      No high-impact coverage in this window yet.</li>`;
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `
      <li class="rounded-xl border border-slate-700/50 bg-slate-900/40 p-3 transition hover:border-slate-600/70">
        <a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer"
           class="article-link group block text-xs font-semibold leading-snug text-slate-200">
          <span class="article-title transition">${esc(item.title)}</span>
        </a>
        <div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-500">
          <span class="font-semibold ${impactClasses(item.impact)}">${esc(item.impact)} impact</span>
          <span>·</span>
          <span>${esc(item.source || "unknown source")}</span>
          <span>·</span>
          <span>${timeAgo(item.published_at)}</span>
          <span class="ml-auto font-semibold ${item.score >= 0 ? "text-emerald-300" : "text-rose-300"}">${signed(item.score)}</span>
        </div>
        ${item.reason ? `<p class="mt-1.5 text-[11px] leading-relaxed text-slate-400">${esc(item.reason)}</p>` : ""}
      </li>`
    )
    .join("");
}

function renderArticles(party, payload) {
  const feed = $(`feed${party}`);
  const count = $(`feed${party}Count`);
  count.textContent = payload.count;

  if (!payload.items.length) {
    const stance = state.stance[party];
    const filterText = stance === "all" ? "" : ` marked ${stance === "for" ? "favorable" : "unfavorable"}`;
    feed.innerHTML = `
      <div class="rounded-xl border border-dashed border-slate-700/60 bg-slate-900/30 px-4 py-8 text-center">
        <p class="text-sm font-semibold text-slate-400">No articles${filterText} yet</p>
        <p class="mt-1 text-xs text-slate-500">Use “Refresh now” to fetch the latest coverage, or widen the window.</p>
      </div>`;
    return;
  }

  feed.innerHTML = payload.items
    .map(
      (item) => `
      <article class="fade-in rounded-xl border border-slate-700/50 bg-slate-900/40 p-3.5 transition hover:border-slate-600/70">
        <div class="flex items-start justify-between gap-3">
          <a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer"
             class="article-link group min-w-0">
            <h4 class="article-title text-sm font-semibold leading-snug text-slate-200 transition">${esc(item.title)}</h4>
          </a>
          <span class="shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-semibold ${stanceClasses(item.score)}">
            ${stanceWord(item.score)}
          </span>
        </div>
        ${item.reason ? `<p class="mt-1.5 text-xs leading-relaxed text-slate-400">${esc(item.reason)}</p>` : ""}
        <div class="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
          <span class="font-medium text-slate-400">${esc(item.source || "unknown source")}</span>
          <span>·</span>
          <span>${timeAgo(item.published_at)}</span>
          <span class="ml-auto inline-flex items-center gap-1.5">
            <span class="h-1.5 w-1.5 rounded-full ${item.impact === "high" ? "bg-amber-400" : item.impact === "medium" ? "bg-sky-400" : "bg-slate-500"}"></span>
            <span class="${impactClasses(item.impact)}">${esc(item.impact)} impact</span>
            <span class="font-semibold ${item.score >= 0 ? "text-emerald-300" : "text-rose-300"}">${signed(item.score)}</span>
          </span>
        </div>
      </article>`
    )
    .join("");
}

function setFeedTab(party, stance) {
  const group = document.querySelector(`[data-feed-tabs="${party}"]`);
  if (!group) return;
  group.querySelectorAll("button[data-stance]").forEach((button) => {
    const active = button.dataset.stance === stance;
    button.className = active
      ? "rounded-md bg-slate-700/70 px-2 py-1 text-slate-100"
      : "rounded-md px-2 py-1 text-slate-400 transition hover:text-slate-200";
  });
}

function setupChart() {
  const canvas = $("trendChart");
  const context = canvas.getContext("2d");
  state.chart = new Chart(context, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Party A",
          data: [],
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56, 189, 248, 0.10)",
          borderWidth: 2.5,
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointBackgroundColor: "#38bdf8",
          fill: true,
        },
        {
          label: "Party B",
          data: [],
          borderColor: "#fb7185",
          backgroundColor: "rgba(251, 113, 133, 0.08)",
          borderWidth: 2.5,
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointBackgroundColor: "#fb7185",
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: {
          min: 0,
          max: 100,
          ticks: { color: "#94a3b8", callback: (value) => `${value}%`, font: { size: 10 } },
          grid: { color: "rgba(148, 163, 184, 0.09)" },
          border: { display: false },
        },
        x: {
          ticks: { color: "#94a3b8", maxTicksLimit: 8, font: { size: 10 } },
          grid: { display: false },
          border: { display: false },
        },
      },
      plugins: {
        legend: {
          labels: { color: "#cbd5e1", usePointStyle: true, boxWidth: 8, boxHeight: 8, font: { size: 11 } },
        },
        tooltip: {
          backgroundColor: "rgba(9, 14, 26, 0.96)",
          borderColor: "rgba(148, 163, 184, 0.25)",
          borderWidth: 1,
          padding: 10,
          callbacks: { label: (item) => `${item.dataset.label}: ${item.parsed.y}%` },
        },
      },
    },
  });
}

function renderHistory(payload) {
  if (!state.chart) return;
  const partyA = state.status ? state.status.party_a : "Party A";
  const partyB = state.status ? state.status.party_b : "Party B";
  state.chart.data.datasets[0].label = partyA;
  state.chart.data.datasets[1].label = partyB;

  const rows = (payload.history || []).filter((row) => row.article_count > 0);
  const empty = rows.length === 0;
  $("chartEmpty").classList.toggle("hidden", !empty);
  $("chartEmpty").classList.toggle("flex", empty);

  state.chart.data.labels = rows.map((row) =>
    new Date(row.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit" })
  );
  state.chart.data.datasets[0].data = rows.map((row) => row.p_a);
  state.chart.data.datasets[1].data = rows.map((row) => row.p_b);
  state.chart.data.datasets.forEach((dataset) => {
    dataset.pointRadius = rows.length === 1 ? 3 : 0;
  });
  state.chart.update();
}

async function loadStatus() {
  try {
    const data = await getJSON("/api/status");
    renderStatus(data);
  } catch (error) {
    console.error(error);
    setText("lastRunText", "API unreachable");
    toast(`Could not load status: ${error.message}`);
  }
}

async function loadHistory() {
  try {
    const data = await getJSON(`/api/history?days=${state.historyDays}`);
    renderHistory(data);
  } catch (error) {
    console.error(error);
  }
}

async function loadArticles(party) {
  try {
    const stance = state.stance[party];
    const data = await getJSON(`/api/articles?party=${party}&stance=${stance}&limit=60`);
    renderArticles(party, data);
  } catch (error) {
    console.error(error);
    $(`feed${party}`).innerHTML = `
      <div class="rounded-xl border border-rose-500/20 bg-rose-500/[0.06] px-4 py-6 text-center text-xs text-rose-200">
        Could not load articles: ${esc(error.message)}
      </div>`;
  }
}

function resetRefreshButton() {
  const button = $("refreshBtn");
  button.disabled = false;
  $("refreshLabel").textContent = "Refresh now";
  $("refreshIcon").classList.remove("animate-spin");
}

async function triggerRefresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  const button = $("refreshBtn");
  button.disabled = true;
  $("refreshLabel").textContent = "Refreshing…";
  $("refreshIcon").classList.add("animate-spin");

  try {
    const result = await getJSON("/api/refresh", { method: "POST" });
    if (!result.started) {
      toast(result.reason || "An update is already running.");
    } else {
      toast("Update started — fetching and scoring the latest news…");
      [4000, 12000, 25000, 45000].forEach((delay) =>
        setTimeout(async () => {
          await loadStatus();
          if (delay >= 25000) {
            await Promise.all([loadHistory(), loadArticles("A"), loadArticles("B")]);
          }
        }, delay)
      );
    }
  } catch (error) {
    toast(`Refresh failed: ${error.message}`);
  } finally {
    state.refreshing = false;
    setTimeout(resetRefreshButton, 2500);
  }
}

function setupEvents() {
  $("refreshBtn").addEventListener("click", triggerRefresh);

  document.querySelectorAll("[data-feed-tabs]").forEach((group) => {
    group.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-stance]");
      if (!button) return;
      const party = group.dataset.feedTabs;
      state.stance[party] = button.dataset.stance;
      setFeedTab(party, button.dataset.stance);
      loadArticles(party);
    });
  });

  document.querySelectorAll(".history-btn").forEach((button) => {
    button.addEventListener("click", () => {
      state.historyDays = Number(button.dataset.historyDays);
      document.querySelectorAll(".history-btn").forEach((other) => {
        other.className = "history-btn rounded-md px-2 py-1 text-slate-400 transition hover:text-slate-200";
      });
      button.className = "history-btn rounded-md bg-slate-700/70 px-2 py-1 text-slate-100";
      loadHistory();
    });
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - state.lastLoaded > 60000) {
      refreshAll();
    }
  });
}

async function refreshAll() {
  state.lastLoaded = Date.now();
  await Promise.all([loadStatus(), loadHistory(), loadArticles("A"), loadArticles("B")]);
}

function init() {
  setupChart();
  setupEvents();
  setFeedTab("A", "all");
  setFeedTab("B", "all");
  refreshAll();
  setInterval(refreshAll, 60000);
}

document.addEventListener("DOMContentLoaded", init);
