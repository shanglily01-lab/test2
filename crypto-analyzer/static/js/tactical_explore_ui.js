/**
 * 战术探索页通用 UI (顶空底多 / 回调做多 / 反弹做空 / 追涨做多 / 杀跌做空)
 * 依赖页面已定义: fmt, fmtSigned, pCls, shortUTC, statusBadge, dirBadge, escapeHtml,
 * buildVerdictsTable, buildRunDetailShell, liveFloatingFromResponse, applyFloatingCards, aiLogBadge
 */
(function (global) {
  var states = {};

  function shellHtml(cfg) {
    var p = cfg.prefix;
    var side = cfg.sideHint || '';
    return (
      '<header class="border-b border-outline-variant/10 px-8 py-5 flex items-center justify-between">' +
      '<div><h1 class="text-2xl font-bold tracking-tight">' + escapeHtml(cfg.pageTitle) + '</h1>' +
      '<p class="text-[11px] text-on-surface-variant mt-1 uppercase tracking-widest">' +
      escapeHtml(cfg.subtitle) + (side ? ' · ' + side : '') +
      ' · 仅模拟仓 · SL 3% / TP 5% / 5x / 4h</p></div>' +
      '<div class="flex items-center gap-4">' +
      '<span class="chip chip-on"><span class="material-symbols-outlined text-[14px]">science</span><span>始终启用</span></span>' +
      '<button type="button" class="tac-run-now flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/20 hover:bg-primary/30 text-xs text-primary" data-tab="' + cfg.tab + '">' +
      '<span class="material-symbols-outlined text-[14px]">play_arrow</span>立即跑一轮</button>' +
      '<button type="button" class="tac-refresh flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-container hover:bg-surface-container-highest text-xs text-on-surface-variant" data-tab="' + cfg.tab + '">' +
      '<span class="material-symbols-outlined text-[14px]">refresh</span>刷新</button></div></header>' +
      '<section class="px-8 py-6 grid grid-cols-5 gap-4">' +
      statCard(p, 'open', '当前持仓') + statCard(p, 'closed', '30 天平仓') +
      statCard(p, 'universe', '上轮 universe') + statCard(p, 'trades', '上轮开仓') +
      '<div class="bg-surface-container-low rounded-xl p-4"><p class="text-[10px] uppercase text-on-surface-variant tracking-wider mb-1">上轮时间</p>' +
      '<p class="mono text-xs font-medium pt-1" id="' + p + '-stat-lastrun">--</p><p class="text-[10px] mt-1" id="' + p + '-stat-laststatus">--</p></div></section>' +
      '<section class="px-8 pb-6 grid grid-cols-5 gap-4">' +
      statCard(p, 'realized', '已实现盈亏') + statCard(p, 'floating', '当前浮盈') +
      statCard(p, 'total', '总盈亏') + statCard(p, 'winrate', '胜率') +
      statCard(p, 'closed2', '30d 平仓数') + '</section>' +
      tableSection(p, 'open', '当前持仓 (OPEN)', 7) +
      tableSection(p, 'runs', '最近运行 (点击展开)', 6) +
      tableSection(p, 'closed', '历史平仓 (30d)', 4, true)
    );
  }

  function statCard(p, key, label) {
    return '<div class="bg-surface-container-low rounded-xl p-4"><p class="text-[10px] uppercase text-on-surface-variant tracking-wider mb-1">' +
      label + '</p><p class="mono text-2xl font-medium" id="' + p + '-stat-' + key + '">--</p></div>';
  }

  function tableSection(p, key, title, cols, isClosed) {
    var tid = p + '-' + key + '-tbody';
    var heads = isClosed
      ? '<th class="px-4 py-2">Symbol</th><th class="px-4 py-2">方向</th><th class="px-4 py-2 text-right">盈亏</th><th class="px-4 py-2">平仓时间</th>'
      : (key === 'runs'
        ? '<th class="px-4 py-2">#</th><th class="px-4 py-2">时间</th><th class="px-4 py-2 text-right">universe</th><th class="px-4 py-2 text-right">开仓</th><th class="px-4 py-2">状态</th><th class="px-4 py-2">摘要</th>'
        : '<th class="px-4 py-2 text-left">Symbol</th><th class="px-4 py-2">方向</th><th class="px-4 py-2 text-right">开仓价</th><th class="px-4 py-2 text-right">现价</th><th class="px-4 py-2 text-right">浮盈</th><th class="px-4 py-2">开仓时间</th><th class="px-4 py-2">催化剂</th>');
    return '<section class="px-8 pb-6"><h2 class="text-sm font-bold uppercase tracking-widest text-on-surface-variant mb-3">' +
      title + '</h2><div class="bg-surface-container-low rounded-xl overflow-x-auto max-h-[380px] overflow-y-auto">' +
      '<table class="w-full text-xs"><thead class="sticky top-0 bg-surface-container-low"><tr class="border-b border-outline-variant/20">' +
      heads + '</tr></thead><tbody id="' + tid + '"><tr><td colspan="' + cols +
      '" class="px-4 py-6 text-center text-on-surface-variant">加载中...</td></tr></tbody></table></div></section>';
  }

  function st(cfg) {
    if (!states[cfg.tab]) {
      states[cfg.tab] = { api: cfg.api, prefix: cfg.prefix, realized: 0, floating: 0, cache: {}, loaded: false };
    }
    return states[cfg.tab];
  }

  function loadStatus(cfg) {
    var s = st(cfg);
    var p = s.prefix;
    fetch(s.api + '/status').then(function (r) { return r.json(); }).then(function (d) {
      var data = d.data || {};
      setText(p + '-stat-open', data.open_positions);
      setText(p + '-stat-closed', data.closed_positions_30d);
      var lr = data.last_run || {};
      setText(p + '-stat-universe', lr.universe_size);
      setText(p + '-stat-trades', lr.trades_opened);
      var el = document.getElementById(p + '-stat-lastrun');
      if (el) el.textContent = lr.asof_utc ? shortUTC(lr.asof_utc) : '--';
      var stEl = document.getElementById(p + '-stat-laststatus');
      if (stEl) stEl.innerHTML = lr.status ? statusBadge(lr.status) : '--';
    }).catch(function (e) { console.error(cfg.tab, e); });
  }

  function setText(id, v) {
    var el = document.getElementById(id);
    if (el) el.textContent = v != null ? v : '--';
  }

  function loadOpen(cfg) {
    var s = st(cfg);
    var p = s.prefix;
    fetch(s.api + '/positions/live').then(function (r) { return r.json(); }).then(function (d) {
      var rows = d.data || [];
      s.floating = liveFloatingFromResponse(d.summary || {}, rows);
      applyFloatingCards(p, s.realized, s.floating);
      var tbody = document.getElementById(p + '-open-tbody');
      if (!tbody) return;
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="px-4 py-6 text-center text-on-surface-variant text-xs">暂无 OPEN</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map(function (row) {
        return '<tr class="border-b border-outline-variant/10"><td class="px-4 py-2 mono">' + escapeHtml(row.symbol) +
          '</td><td class="px-4 py-2">' + dirBadge(row.position_side) + '</td>' +
          '<td class="px-4 py-2 text-right mono">' + fmt(row.entry_price, 6) + '</td>' +
          '<td class="px-4 py-2 text-right mono">' + fmt(row.mark_price, 6) + '</td>' +
          '<td class="px-4 py-2 text-right mono ' + pCls(row.unrealized_pnl) + '">' + fmtSigned(row.unrealized_pnl) + '</td>' +
          '<td class="px-4 py-2 text-[11px]">' + shortUTC(row.open_time) + '</td>' +
          '<td class="px-4 py-2 text-[11px] truncate max-w-xs" title="' + escapeHtml(row.entry_reason || '') + '">' +
          escapeHtml(row.entry_reason || '') + '</td></tr>';
      }).join('');
    });
  }

  function loadStats(cfg) {
    var s = st(cfg);
    var p = s.prefix;
    fetch(s.api + '/stats?days=30').then(function (r) { return r.json(); }).then(function (d) {
      if (!d.success) return;
      var data = d.data || {};
      s.realized = data.total_realized_pnl || 0;
      var re = document.getElementById(p + '-stat-realized');
      if (re) re.innerHTML = '<span class="' + pCls(data.total_realized_pnl) + '">' + fmtSigned(data.total_realized_pnl) + ' U</span>';
      applyFloatingCards(p, s.realized, s.floating);
      var wr = document.getElementById(p + '-stat-winrate');
      if (wr) wr.innerHTML = '<span class="' + ((data.win_rate || 0) >= 50 ? 'pp' : 'pn') + '">' + fmt(data.win_rate, 1) + '%</span>';
      setText(p + '-stat-closed2', data.total_trades);
    });
  }

  function loadRuns(cfg) {
    var s = st(cfg);
    var p = s.prefix;
    fetch(s.api + '/runs?limit=20').then(function (r) { return r.json(); }).then(function (d) {
      var rows = d.data || [];
      var tbody = document.getElementById(p + '-runs-tbody');
      if (!tbody) return;
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-6 text-center text-on-surface-variant text-xs">暂无记录</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map(function (r) {
        return '<tr class="border-b border-outline-variant/10 cursor-pointer hover:bg-surface-container/40" data-tac-run="' + cfg.tab + '" data-run-id="' + r.id + '">' +
          '<td class="px-4 py-2 mono">' + r.id + aiLogBadge(r) + '</td>' +
          '<td class="px-4 py-2 text-[11px]">' + shortUTC(r.asof_utc) + '</td>' +
          '<td class="px-4 py-2 text-right mono">' + (r.universe_size || 0) + '</td>' +
          '<td class="px-4 py-2 text-right mono">' + (r.trades_opened || 0) + '</td>' +
          '<td class="px-4 py-2">' + statusBadge(r.status) + '</td>' +
          '<td class="px-4 py-2 text-[11px] truncate max-w-md">' + escapeHtml(r.summary_short || r.error_msg || '') + '</td></tr>' +
          '<tr id="' + p + '-verdict-row-' + r.id + '" class="hidden"><td colspan="6" class="px-6 py-4" id="' + p + '-verdict-cell-' + r.id + '"></td></tr>';
      }).join('');
    });
  }

  function toggleVerdicts(cfg, runId) {
    var s = st(cfg);
    var p = s.prefix;
    var row = document.getElementById(p + '-verdict-row-' + runId);
    if (!row) return;
    if (!row.classList.contains('hidden')) { row.classList.add('hidden'); return; }
    var cell = document.getElementById(p + '-verdict-cell-' + runId);
    if (s.cache[runId]) { cell.innerHTML = s.cache[runId]; row.classList.remove('hidden'); return; }
    cell.innerHTML = '<p class="text-xs text-on-surface-variant">加载中...</p>';
    row.classList.remove('hidden');
    fetch(s.api + '/verdicts?run_id=' + runId).then(function (r) { return r.json(); }).then(function (d) {
      var html = buildRunDetailShell(runId, p + '-', s.api, buildVerdictsTable(d.data || [], false, true));
      s.cache[runId] = html;
      cell.innerHTML = html;
    });
  }

  function loadClosed(cfg) {
    var s = st(cfg);
    var p = s.prefix;
    fetch(s.api + '/positions?status=closed&limit=100').then(function (r) { return r.json(); }).then(function (d) {
      var rows = d.data || [];
      var tbody = document.getElementById(p + '-closed-tbody');
      if (!tbody) return;
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center text-on-surface-variant text-xs">近 30 天无平仓</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map(function (row) {
        return '<tr class="border-b border-outline-variant/10"><td class="px-4 py-2 mono">' + escapeHtml(row.symbol) +
          '</td><td class="px-4 py-2">' + dirBadge(row.position_side) + '</td>' +
          '<td class="px-4 py-2 text-right mono ' + pCls(row.realized_pnl) + '">' + fmtSigned(row.realized_pnl) + '</td>' +
          '<td class="px-4 py-2 text-[11px]">' + shortUTC(row.close_time) + '</td></tr>';
      }).join('');
    });
  }

  function loadAll(cfg) {
    loadStatus(cfg);
    loadOpen(cfg);
    loadRuns(cfg);
    loadClosed(cfg);
    loadStats(cfg);
    st(cfg).loaded = true;
  }

  function runNow(cfg) {
    fetch(st(cfg).api + '/run-now', { method: 'POST' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) { alert(x.j.detail || '启动失败'); return; }
        alert(x.j.message || '已启动');
        setTimeout(function () { loadAll(cfg); }, 3000);
      });
  }

  function init(configs) {
    configs.forEach(function (cfg) {
      var el = document.getElementById('tab-' + cfg.tab);
      if (el && !el.dataset.tacMounted) {
        el.innerHTML = shellHtml(cfg);
        el.dataset.tacMounted = '1';
      }
      st(cfg);
    });

    document.addEventListener('click', function (ev) {
      var runBtn = ev.target.closest('.tac-run-now');
      if (runBtn) {
        var tab = runBtn.getAttribute('data-tab');
        var c = configs.find(function (x) { return x.tab === tab; });
        if (c) runNow(c);
        return;
      }
      var refBtn = ev.target.closest('.tac-refresh');
      if (refBtn) {
        var tab2 = refBtn.getAttribute('data-tab');
        var c2 = configs.find(function (x) { return x.tab === tab2; });
        if (c2) loadAll(c2);
        return;
      }
      var runRow = ev.target.closest('[data-tac-run]');
      if (runRow) {
        var tab3 = runRow.getAttribute('data-tac-run');
        var rid = runRow.getAttribute('data-run-id');
        var c3 = configs.find(function (x) { return x.tab === tab3; });
        if (c3) toggleVerdicts(c3, parseInt(rid, 10));
      }
    });

    setInterval(function () {
      configs.forEach(function (cfg) {
        var tabEl = document.getElementById('tab-' + cfg.tab);
        if (tabEl && !tabEl.classList.contains('hidden') && st(cfg).loaded) {
          loadOpen(cfg);
        }
      });
    }, 5000);
  }

  function onTabShow(cfg) {
    if (!st(cfg).loaded) loadAll(cfg);
    else loadOpen(cfg);
  }

  global.TacticalExploreUI = { init: init, onTabShow: onTabShow, loadAll: loadAll };
})(window);
