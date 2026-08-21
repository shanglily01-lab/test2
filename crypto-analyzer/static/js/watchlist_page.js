(function () {
  const API = "/api/watchlist";
  const FSTREAM_WS = "wss://fstream.binance.com/stream?streams=";
  let side = "LONG";
  let kind = "limit";
  let symbols = [];
  let cleanToCanon = {};
  let ws = null;
  let wsKey = "";
  let reconnectTimer = null;
  let fallbackTimer = null;
  let bookTimer = null;
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

  function fmtPct(v) {
    if (v == null || v === "") return "--";
    const n = Number(v);
    if (!Number.isFinite(n)) return "--";
    return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
  }

  function cleanSym(sym) {
    return String(sym || "").toUpperCase().replace(/[/_-]/g, "");
  }

  function setPriceChip(text, ok) {
    const chip = $("refresh-chip");
    chip.textContent = text;
    chip.className = "chip " + (ok ? "chip-on" : "chip-off");
  }

  async function jget(url) {
    const r = await fetch(url);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    return d;
  }
  async function jsend(url, method, body) {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : (d.detail && d.detail[0] && d.detail[0].msg) || r.statusText);
    return d;
  }

  function selectSymbol(sym, price) {
    $("ord-symbol").value = sym;
    if (price && kind === "limit") $("ord-limit").value = fmtPx(price);
  }

  function applyTick(canon, price, chg) {
    if (!canon || !Number.isFinite(price) || price <= 0) return;
    lastPrices[canon] = price;
    const row = document.querySelector('tr[data-symbol="' + canon.replace(/"/g, "") + '"]');
    if (row) {
      const pxEl = row.querySelector("[data-role=px]");
      const chEl = row.querySelector("[data-role=chg]");
      if (pxEl) pxEl.textContent = fmtPx(price);
      if (chEl && chg != null && Number.isFinite(chg)) {
        chEl.textContent = fmtPct(chg);
        chEl.className = "px-4 py-3 text-right mono " + (chg >= 0 ? "text-primary" : "text-error");
      }
    }
    document.querySelectorAll('[data-pos-symbol="' + canon.replace(/"/g, "") + '"]').forEach(function (el) {
      const qty = Number(el.getAttribute("data-qty"));
      const entry = Number(el.getAttribute("data-entry"));
      const posSide = el.getAttribute("data-side");
      const pnlEl = el.querySelector("[data-role=pnl]");
      if (!Number.isFinite(qty) || !Number.isFinite(entry) || !pnlEl) return;
      let pnl = (price - entry) * qty;
      if (posSide === "SHORT") pnl = (entry - price) * qty;
      pnlEl.textContent = fmtPx(pnl) + " U";
      pnlEl.className = "mono " + (pnl >= 0 ? "text-primary" : "text-error");
    });
  }

  function renderItems(items) {
    const tb = $("watch-body");
    const empty = $("watch-empty");
    tb.innerHTML = "";
    symbols = [];
    cleanToCanon = {};
    if (!items || !items.length) {
      empty.classList.remove("hidden");
      connectPriceWs([]);
      return;
    }
    empty.classList.add("hidden");
    items.forEach(function (it) {
      symbols.push(it.symbol);
      cleanToCanon[cleanSym(it.symbol)] = it.symbol;
      const tr = document.createElement("tr");
      tr.setAttribute("data-symbol", it.symbol);
      tr.className = "border-t border-outline-variant/10 hover:bg-surface-container cursor-pointer";
      const ch = Number(it.change_24h);
      const chCls = Number.isFinite(ch) ? (ch >= 0 ? "text-primary" : "text-error") : "";
      const live = it.live_sync_ok
        ? '<span class="chip chip-on">可同步</span>'
        : '<span class="chip chip-off">仅模拟</span>';
      const lv = it.rating_level == null ? "未评级" : ("L" + it.rating_level);
      tr.innerHTML =
        '<td class="px-4 py-3 font-semibold">' + it.symbol + "</td>" +
        '<td class="px-4 py-3 text-right mono" data-role="px">' + fmtPx(it.price) + "</td>" +
        '<td class="px-4 py-3 text-right mono ' + chCls + '" data-role="chg">' + fmtPct(it.change_24h) + "</td>" +
        '<td class="px-4 py-3">' + lv + (it.rating_locked ? " 锁" : "") + "</td>" +
        '<td class="px-4 py-3">' + live + "</td>" +
        '<td class="px-4 py-3 text-right"><button data-del="' + it.symbol + '" class="text-xs text-on-surface-variant hover:text-error">移除</button></td>';
      tr.addEventListener("click", function (ev) {
        if (ev.target && ev.target.getAttribute("data-del")) return;
        selectSymbol(it.symbol, lastPrices[it.symbol] || it.price);
      });
      const del = tr.querySelector("[data-del]");
      del.addEventListener("click", async function (ev) {
        ev.stopPropagation();
        await jsend(API + "/" + encodeURIComponent(it.symbol), "DELETE");
        await loadAll();
      });
      tb.appendChild(tr);
      if (it.price) lastPrices[it.symbol] = Number(it.price);
    });
    connectPriceWs(symbols);
  }

  function renderBook(data) {
    const pos = data.positions || [];
    const ords = data.orders || [];
    $("pos-box").innerHTML = pos.length
      ? pos.map(function (p) {
          const u = Number(p.unrealized_pnl);
          const cls = u >= 0 ? "text-primary" : "text-error";
          return '<div class="flex justify-between gap-2 py-2 border-b border-outline-variant/10" data-pos-symbol="' + p.symbol +
            '" data-side="' + (p.position_side || "") +
            '" data-qty="' + p.quantity +
            '" data-entry="' + p.entry_price + '">' +
            "<span>" + p.symbol + ' <span class="' + (p.position_side === "LONG" ? "text-primary" : "text-error") + '">' + p.position_side + "</span></span>" +
            '<span class="mono ' + cls + '" data-role="pnl">' + fmtPx(p.unrealized_pnl) + " U</span>" +
            '<a class="text-xs text-secondary" href="/futures_trading">去合约页平仓</a></div>';
        }).join("")
      : "暂无自选持仓";
    $("ord-box").innerHTML = ords.length
      ? ords.map(function (o) {
          return '<div class="flex justify-between gap-2 py-2 border-b border-outline-variant/10">' +
            "<span>" + o.symbol + " " + o.side + " " + o.order_type + "</span>" +
            '<span class="mono">' + fmtPx(o.price) + "</span>" +
            '<span class="text-xs">' + o.status + "</span></div>";
        }).join("")
      : "暂无自选订单";
    pos.forEach(function (p) {
      const live = lastPrices[p.symbol];
      if (live) applyTick(p.symbol, live, null);
    });
  }

  function stopFallback() {
    if (fallbackTimer) {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }
  }

  function startFallback() {
    if (fallbackTimer) return;
    fallbackTimer = setInterval(function () {
      loadSnapshotPrices().catch(function () {});
    }, 5000);
  }

  function closePriceWs() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      try {
        ws.onclose = null;
        ws.close();
      } catch (e) {}
      ws = null;
    }
    wsKey = "";
  }

  function connectPriceWs(list) {
    const cleans = (list || []).map(function (s) { return cleanSym(s).toLowerCase(); }).filter(Boolean);
    const key = cleans.slice().sort().join(",");
    if (!cleans.length) {
      closePriceWs();
      stopFallback();
      setPriceChip("无自选", false);
      return;
    }
    if (key === wsKey && ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    closePriceWs();
    wsKey = key;
    const streams = cleans.map(function (c) { return c + "@miniTicker"; }).join("/");
    const socket = new WebSocket(FSTREAM_WS + streams);
    ws = socket;
    socket.onopen = function () {
      if (ws !== socket) return;
      stopFallback();
      setPriceChip("WS 实时", true);
    };
    socket.onmessage = function (ev) {
      if (ws !== socket) return;
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      const d = msg.data || msg;
      if (!d || !d.s) return;
      const canon = cleanToCanon[String(d.s).toUpperCase()];
      if (!canon) return;
      const last = Number(d.c);
      const open = Number(d.o);
      const chg = open > 0 ? ((last - open) / open) * 100 : null;
      applyTick(canon, last, chg);
    };
    socket.onerror = function () {
      if (ws !== socket) return;
      setPriceChip("WS 异常 · 回退", false);
    };
    socket.onclose = function () {
      if (ws !== socket) return;
      ws = null;
      setPriceChip("WS 断开 · 回退", false);
      startFallback();
      reconnectTimer = setTimeout(function () {
        wsKey = "";
        connectPriceWs(symbols);
      }, 2000);
    };
  }

  async function loadSnapshotPrices() {
    const d = await jget(API);
    (d.items || []).forEach(function (it) {
      applyTick(it.symbol, Number(it.price), it.change_24h == null ? null : Number(it.change_24h));
    });
  }

  async function loadUniverse(q) {
    const d = await jget(API + "/universe?q=" + encodeURIComponent(q || ""));
    const dl = $("universe-list");
    dl.innerHTML = (d.symbols || []).map(function (s) {
      return '<option value="' + s + '"></option>';
    }).join("");
  }

  async function loadBook() {
    const book = await jget(API + "/orders");
    renderBook(book);
  }

  async function loadAll() {
    const d = await jget(API);
    const chip = $("live-chip");
    if (d.live_trading_enabled) {
      chip.textContent = "实盘开 · 成交瞬间同步 · 仅 L0";
      chip.className = "chip chip-on";
    } else {
      chip.textContent = "实盘关 · 仅模拟";
      chip.className = "chip chip-off";
    }
    if (d.defaults) {
      if (!$("ord-lev").dataset.touched) $("ord-lev").value = d.defaults.leverage;
      if (!$("ord-margin").dataset.touched) $("ord-margin").value = d.defaults.margin;
      if (!$("ord-sl").dataset.touched) $("ord-sl").value = d.defaults.sl_pct;
      if (!$("ord-tp").dataset.touched) $("ord-tp").value = d.defaults.tp_pct;
    }
    renderItems(d.items || []);
    await loadBook();
  }

  function setSide(v) {
    side = v;
    document.querySelectorAll(".side-btn").forEach(function (b) {
      const on = b.getAttribute("data-side") === v;
      b.className = "side-btn px-3 py-2 rounded-lg text-sm font-semibold " +
        (on ? (v === "LONG" ? "bg-primary/15 text-primary border border-primary/30" : "bg-error/15 text-error border border-error/30")
          : "bg-surface-container text-on-surface-variant");
    });
  }
  function setKind(v) {
    kind = v;
    document.querySelectorAll(".kind-btn").forEach(function (b) {
      const on = b.getAttribute("data-kind") === v;
      b.className = "kind-btn px-3 py-2 rounded-lg text-xs font-semibold " +
        (on ? "bg-primary/15 text-primary" : "bg-surface-container text-on-surface-variant");
    });
    $("limit-wrap").classList.toggle("hidden", v === "market");
  }

  $("btn-add").addEventListener("click", async function () {
    const sym = $("add-symbol").value.trim();
    if (!sym) return;
    try {
      await jsend(API, "POST", { symbol: sym });
      $("add-symbol").value = "";
      await loadAll();
    } catch (e) {
      alert(e.message);
    }
  });
  $("add-symbol").addEventListener("input", function () {
    loadUniverse($("add-symbol").value).catch(function () {});
  });
  $("btn-refresh").addEventListener("click", function () {
    loadAll().catch(function (e) { alert(e.message); });
  });
  document.querySelectorAll(".side-btn").forEach(function (b) {
    b.addEventListener("click", function () { setSide(b.getAttribute("data-side")); });
  });
  document.querySelectorAll(".kind-btn").forEach(function (b) {
    b.addEventListener("click", function () { setKind(b.getAttribute("data-kind")); });
  });
  ["ord-lev", "ord-margin", "ord-sl", "ord-tp"].forEach(function (id) {
    $(id).addEventListener("input", function () { $(id).dataset.touched = "1"; });
  });
  $("btn-order").addEventListener("click", async function () {
    const msg = $("ord-msg");
    msg.textContent = "提交中…";
    try {
      const body = {
        symbol: $("ord-symbol").value,
        side: side,
        order_type: kind,
        leverage: Number($("ord-lev").value),
        margin: Number($("ord-margin").value),
        sl_pct: Number($("ord-sl").value),
        tp_pct: Number($("ord-tp").value),
      };
      if (kind === "limit") body.limit_price = Number($("ord-limit").value);
      const d = await jsend(API + "/order", "POST", body);
      msg.textContent = d.message || "已提交";
      await loadAll();
    } catch (e) {
      msg.textContent = e.message;
    }
  });

  window.addEventListener("beforeunload", function () {
    closePriceWs();
    stopFallback();
    if (bookTimer) clearInterval(bookTimer);
  });

  setSide("LONG");
  setKind("limit");
  loadUniverse("").catch(function () {});
  loadAll().then(function () {
    bookTimer = setInterval(function () { loadBook().catch(function () {}); }, 30000);
  }).catch(function (e) {
    $("watch-empty").classList.remove("hidden");
    $("watch-empty").textContent = e.message;
  });
})();
