(function () {
  const SIGNALS_API = "/api/technical-signals";
  const PRICES_API = "/api/technical-signals/prices";
  const SIGNAL_MS = 15000;
  const PRICE_MS = 1000;
  let allSignals = [];
  let lastPrices = {};

  function $(id) { return document.getElementById(id); }

  function fmtPx(v) {
    if (v == null || v === "") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    if (n >= 100) return n.toFixed(2);
    if (n >= 1) return n.toFixed(4);
    return n.toPrecision(6);
  }

  function scoreColorClass(score) {
    if (score >= 80) return "text-primary";
    if (score >= 60) return "text-secondary";
    return "text-error";
  }

  function scoreBarClass(score) {
    if (score >= 80) return "bg-primary";
    if (score >= 60) return "bg-secondary";
    return "bg-error";
  }

  function formatTime(ts) {
    if (!ts) return "--";
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts).slice(11, 19) || ts;
    const h = String(d.getHours()).padStart(2, "0");
    const m = String(d.getMinutes()).padStart(2, "0");
    const s = String(d.getSeconds()).padStart(2, "0");
    return h + ":" + m + ":" + s;
  }

  function setStatus(text, ok) {
    const el = $("signals-status");
    if (!el) return;
    el.textContent = text;
    el.className = "px-3 py-1 text-[10px] font-mono font-bold uppercase tracking-tighter " +
      (ok ? "text-primary" : "text-error");
  }

  function filteredRows() {
    const searchVal = (($("symbol-search") && $("symbol-search").value) || "").trim().toUpperCase();
    const dirVal = (($("direction-filter") && $("direction-filter").value) || "").trim().toUpperCase();
    const minScore = Number(($("min-score") && $("min-score").value) || 0);
    return allSignals.filter(function (item) {
      const sym = (item.symbol || "").toUpperCase();
      const dir = (item.direction || "").toUpperCase();
      const score = parseFloat(item.total_score) || 0;
      if (searchVal && sym.indexOf(searchVal) === -1) return false;
      if (dirVal && dir !== dirVal) return false;
      if (Number.isFinite(minScore) && score < minScore) return false;
      return true;
    });
  }

  function applyPrice(symbol, price) {
    if (!symbol || !Number.isFinite(price) || price <= 0) return;
    lastPrices[symbol] = price;
    const row = document.querySelector('tr[data-symbol="' + String(symbol).replace(/"/g, "") + '"]');
    if (!row) return;
    const el = row.querySelector("[data-role=px]");
    if (el) el.textContent = fmtPx(price);
  }

  function renderTable() {
    const tbody = $("signals-tbody");
    if (!tbody) return;
    const filtered = filteredRows();
    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="10" class="px-6 py-10 text-center text-on-surface-variant text-sm">暂无数据</td></tr>';
      return;
    }
    tbody.innerHTML = filtered.map(function (item) {
      const sym = item.symbol || "--";
      const dir = (item.direction || "").toUpperCase();
      const score = parseFloat(item.total_score) || 0;
      const scoreDisp = score.toFixed(0);
      const colorClass = scoreColorClass(score);
      const barClass = scoreBarClass(score);
      const barPct = Math.min(100, Math.max(0, score)).toFixed(0);
      const timeDisp = formatTime(item.updated_at);
      const initial = String(sym).charAt(0);
      const px = lastPrices[sym] != null ? lastPrices[sym] : item.price;
      const dirBadge = dir === "LONG"
        ? '<span class="px-2 py-1 rounded bg-primary/10 text-primary text-[10px] font-bold tracking-widest uppercase">LONG</span>'
        : dir === "SHORT"
        ? '<span class="px-2 py-1 rounded bg-error/10 text-error text-[10px] font-bold tracking-widest uppercase">SHORT</span>'
        : '<span class="px-2 py-1 rounded bg-surface-container text-on-surface-variant text-[10px] font-bold tracking-widest uppercase">' + (dir || "--") + "</span>";
      const h1Score = item.h1_score != null ? item.h1_score : "--";
      const m15Score = item.m15_score != null ? item.m15_score : "--";
      const h1BullStr = item.h1_bullish != null
        ? '<span class="text-primary">' + item.h1_bullish + '</span>/<span class="text-error">' + item.h1_bearish + "</span>"
        : "--";
      const m15BullStr = item.m15_bullish != null
        ? '<span class="text-primary">' + item.m15_bullish + '</span>/<span class="text-error">' + item.m15_bearish + "</span>"
        : "--";
      const m5BullStr = item.m5_bullish != null
        ? '<span class="text-primary">' + item.m5_bullish + '</span>/<span class="text-error">' + item.m5_bearish + "</span>"
        : "--";
      const h1Class = h1Score > 0 ? "text-primary" : h1Score < 0 ? "text-error" : "text-on-surface-variant";
      const m15Class = m15Score > 0 ? "text-primary" : m15Score < 0 ? "text-error" : "text-on-surface-variant";
      return '<tr class="hover:bg-surface-container/50 transition-colors" data-symbol="' + String(sym).replace(/"/g, "") + '">'
        + '<td class="px-6 py-4"><div class="flex items-center gap-2">'
        + '<div class="w-6 h-6 rounded-full bg-surface-container-highest flex items-center justify-center text-[10px] font-bold text-on-surface">' + initial + "</div>"
        + '<span class="text-sm font-bold score-mono">' + sym + "</span></div></td>"
        + '<td class="px-4 py-4 text-right score-mono" data-role="px">' + fmtPx(px) + "</td>"
        + '<td class="px-6 py-4">' + dirBadge + "</td>"
        + '<td class="px-6 py-4"><div class="flex items-center gap-3">'
        + '<span class="text-sm font-bold ' + colorClass + ' score-mono">' + scoreDisp + "</span>"
        + '<div class="w-16 h-1 bg-surface-container-highest rounded-full overflow-hidden">'
        + '<div class="h-full ' + barClass + '" style="width:' + barPct + '%"></div></div></div></td>'
        + '<td class="px-4 py-4 text-xs score-mono ' + h1Class + '">' + h1Score + "</td>"
        + '<td class="px-4 py-4 text-xs score-mono ' + m15Class + '">' + m15Score + "</td>"
        + '<td class="px-4 py-4 text-xs">' + h1BullStr + "</td>"
        + '<td class="px-4 py-4 text-xs">' + m15BullStr + "</td>"
        + '<td class="px-4 py-4 text-xs">' + m5BullStr + "</td>"
        + '<td class="px-6 py-4 text-xs text-right text-on-surface-variant score-mono">' + timeDisp + "</td>"
        + "</tr>";
    }).join("");
  }

  function updateDistribution() {
    const buckets = [
      { min: 80, max: 101, pctId: "dist-80-pct", barId: "dist-80-bar" },
      { min: 60, max: 80, pctId: "dist-60-pct", barId: "dist-60-bar" },
      { min: 40, max: 60, pctId: "dist-40-pct", barId: "dist-40-bar" },
      { min: 20, max: 40, pctId: "dist-20-pct", barId: "dist-20-bar" },
      { min: 0, max: 20, pctId: "dist-0-pct", barId: "dist-0-bar" },
    ];
    const n = allSignals.length || 1;
    buckets.forEach(function (b) {
      const c = allSignals.filter(function (it) {
        const s = parseFloat(it.total_score) || 0;
        return s >= b.min && s < b.max;
      }).length;
      const pct = Math.round((c / n) * 100);
      const pctEl = $(b.pctId);
      const barEl = $(b.barId);
      if (pctEl) pctEl.textContent = pct + "%";
      if (barEl) barEl.style.width = pct + "%";
    });
  }

  function updateTopSignals() {
    const scoreList = $("score-list");
    if (!scoreList) return;
    const sorted = allSignals.slice().sort(function (a, b) {
      return (parseFloat(b.total_score) || 0) - (parseFloat(a.total_score) || 0);
    }).slice(0, 10);
    if (!sorted.length) {
      scoreList.innerHTML = '<div class="text-xs text-on-surface-variant text-center py-4">暂无数据</div>';
      return;
    }
    scoreList.innerHTML = sorted.map(function (item) {
      const sym = (item.symbol || "--").replace("/USDT", "").replace("/USD", "");
      const dir = (item.direction || "").toUpperCase();
      const score = parseFloat(item.total_score) || 0;
      const colorClass = scoreColorClass(score);
      const borderColor = score >= 80 ? "border-primary" : "border-secondary";
      const dirColor = dir === "LONG" ? "text-primary" : dir === "SHORT" ? "text-error" : "text-on-surface-variant";
      const confidence = score >= 80 ? "HIGH" : score >= 60 ? "MED" : "LOW";
      return '<div class="flex items-center justify-between p-3 rounded-xl bg-surface-container border-l-2 ' + borderColor + '">'
        + '<div class="flex items-center gap-3">'
        + '<span class="text-sm font-bold score-mono">' + sym + "</span>"
        + '<span class="text-[10px] font-bold ' + dirColor + '">' + (dir || "--") + "</span></div>"
        + '<div class="text-right"><div class="text-sm font-bold ' + colorClass + ' score-mono">' + score.toFixed(1) + "</div>"
        + '<div class="text-[8px] text-on-surface-variant">CONFIDENCE: ' + confidence + "</div></div></div>";
    }).join("");
  }

  async function loadSignals() {
    try {
      const resp = await fetch(SIGNALS_API);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const result = await resp.json();
      const arr = Array.isArray(result.table) ? result.table
        : (Array.isArray(result.data) ? result.data : []);
      if (!arr.length) {
        setStatus("缓存为空", false);
        allSignals = [];
      } else {
        allSignals = arr.map(function (it) {
          if (it.price) lastPrices[it.symbol] = Number(it.price);
          return it;
        });
        const n = allSignals.length;
        const ts = result.timestamp ? formatTime(result.timestamp) : formatTime((allSignals[0] || {}).updated_at);
        setStatus("实时 · " + n + " 对 · " + ts, true);
      }
    } catch (e) {
      setStatus("加载失败", false);
      console.error("loadSignals", e);
    }
    renderTable();
    updateTopSignals();
    updateDistribution();
  }

  async function loadPrices() {
    try {
      const resp = await fetch(PRICES_API);
      if (!resp.ok) return;
      const result = await resp.json();
      (result.items || []).forEach(function (it) {
        applyPrice(it.symbol, Number(it.price));
      });
    } catch (e) {}
  }

  document.addEventListener("DOMContentLoaded", function () {
    const searchInput = $("symbol-search");
    const dirSelect = $("direction-filter");
    const minScore = $("min-score");
    const minLabel = $("min-score-label");
    if (searchInput) searchInput.addEventListener("input", renderTable);
    if (dirSelect) dirSelect.addEventListener("change", renderTable);
    if (minScore) {
      minScore.addEventListener("input", function () {
        if (minLabel) minLabel.textContent = minScore.value;
        renderTable();
      });
    }
    const refreshBtn = $("signals-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", function () {
      loadSignals();
      loadPrices();
    });
    loadSignals();
    loadPrices();
    setInterval(loadSignals, SIGNAL_MS);
    setInterval(loadPrices, PRICE_MS);
  });
})();
