(function () {
  const API = "/api/market-regime/live";

  function $(id) { return document.getElementById(id); }

  function fmtPct(v, digits) {
    if (v == null || v === "") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    const d = digits == null ? 2 : digits;
    return (n >= 0 ? "+" : "") + n.toFixed(d) + "%";
  }

  function fmtNum(v, digits) {
    if (v == null || v === "") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    return n.toFixed(digits == null ? 2 : digits);
  }

  function pctClass(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return "";
    return n >= 0 ? "text-primary" : "text-error";
  }

  function biasChip(bias, ok) {
    const b = String(bias || "FLAT").toUpperCase();
    if (!ok) return '<span class="chip chip-off">疲软 · 少开</span>';
    if (b === "LONG") return '<span class="chip chip-on">偏多 LONG</span>';
    if (b === "SHORT") return '<span class="chip chip-warn">偏空 SHORT</span>';
    return '<span class="chip chip-off">不明 FLAT</span>';
  }

  function coinDir(chg) {
    const n = Number(chg);
    if (!Number.isFinite(n)) return '<span class="chip chip-off">--</span>';
    if (n >= 0.8) return '<span class="chip chip-on">多</span>';
    if (n <= -0.8) return '<span class="chip chip-warn">空</span>';
    return '<span class="chip chip-off">弱</span>';
  }

  function render(d) {
    const b4 = d.big4 || {};
    const daily = d.daily || {};
    const btc = daily.btc || {};
    const eth = daily.eth || {};
    $("live-chip").innerHTML = biasChip(b4.bias, b4.big4_ok);
    $("big4-title").textContent = b4.big4_ok ? ("Big4 " + (b4.bias || "FLAT")) : "Big4 疲软";
    $("big4-sub").textContent =
      "近 6 小时 1h：多 " + (b4.bull_count || 0) +
      " / 空 " + (b4.bear_count || 0) +
      " / 疲软 " + (b4.weak_count || 0) +
      (b4.reason ? " · " + b4.reason : "");
    $("daily-title").textContent = daily.title || daily.global_regime || "--";
    $("daily-code").textContent = daily.global_regime || "";
    $("daily-meaning").textContent = daily.meaning || daily.reason || "";
    $("btc-pos").textContent = btc.range_pos_90d == null ? "--" : (Number(btc.range_pos_90d) * 100).toFixed(0) + "%";
    $("btc-7d").textContent = fmtPct(btc.change_7d_pct);
    $("btc-7d").className = "mono " + pctClass(btc.change_7d_pct);
    $("btc-30d").textContent = fmtPct(btc.change_30d_pct);
    $("btc-30d").className = "mono " + pctClass(btc.change_30d_pct);
    $("btc-90d").textContent = fmtPct(btc.change_90d_pct);
    $("btc-90d").className = "mono " + pctClass(btc.change_90d_pct);
    $("btc-ema").textContent = btc.ema_bull ? "多头排列" : (btc.ema_bear ? "空头排列" : "纠缠");
    $("eth-30d").textContent = fmtPct(eth.change_30d_pct);
    $("eth-30d").className = "mono " + pctClass(eth.change_30d_pct);

    const tb = $("coin-body");
    tb.innerHTML = "";
    (b4.per_coin || []).forEach(function (c) {
      const tr = document.createElement("tr");
      tr.className = "border-t border-outline-variant/10";
      const chg = c.change_6h_pct;
      tr.innerHTML =
        '<td class="px-4 py-3 font-semibold">' + (c.symbol || "") + "</td>" +
        '<td class="px-4 py-3">' + coinDir(chg) + "</td>" +
        '<td class="px-4 py-3 text-right mono ' + pctClass(chg) + '">' + fmtPct(chg, 2) + "</td>" +
        '<td class="px-4 py-3 text-right mono">' + fmtNum(c.rel_volume, 2) + "</td>" +
        '<td class="px-4 py-3">' + (c.weak ? '<span class="chip chip-off">疲软</span>' : '<span class="chip chip-on">有动能</span>') + "</td>";
      tb.appendChild(tr);
    });

    const gates = d.gates || [];
    $("gate-box").innerHTML = gates.length
      ? gates.map(function (g) { return "<li>" + g + "</li>"; }).join("")
      : "<li>暂无闸门说明</li>";

    const now = new Date();
    const pad = function (n) { return n < 10 ? "0" + n : String(n); };
    $("last-update-time").textContent =
      "刷新 " + now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate()) +
      " " + pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
  }

  async function load() {
    const r = await fetch(API);
    const j = await r.json().catch(function () { return {}; });
    if (!r.ok || j.ok === false) throw new Error(j.detail || j.message || r.statusText);
    render(j);
  }

  $("btn-refresh").addEventListener("click", function () {
    load().catch(function (e) { $("err").textContent = e.message; });
  });

  load().then(function () {
    setInterval(function () { load().catch(function () {}); }, 30000);
  }).catch(function (e) {
    $("err").textContent = e.message;
  });
})();
