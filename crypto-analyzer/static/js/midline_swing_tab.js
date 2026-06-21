/**
 * 中线做多/做空 Tab — Gemini / DeepSeek 探索页共用（量化引擎，非 LLM）
 * MidlineSwingTab.init({ teacher: 'gemini' | 'deepseek' })
 */
(function (global) {
  var API = '/api/midline-swing';
  var PANEL_VERSION = '2';

  function esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmt(n, d) {
    if (n == null || n === '') return '--';
    d = d == null ? 2 : d;
    return Number(n).toFixed(d);
  }

  function fmtSigned(n) {
    if (n == null) return '--';
    var v = Number(n);
    return (v >= 0 ? '+' : '') + v.toFixed(2);
  }

  function pCls(v) {
    return (v || 0) >= 0 ? 'pp' : 'pn';
  }

  function dirBadge(side) {
    var u = (side || '').toUpperCase();
    if (u === 'LONG') return '<span class="chip chip-long">LONG</span>';
    if (u === 'SHORT') return '<span class="chip chip-short">SHORT</span>';
    return esc(side);
  }

  function statusBadge(st) {
    if (st === 'ok') return '<span class="chip chip-on">ok</span>';
    if (st === 'error') return '<span class="chip" style="color:#ff716c">error</span>';
    if (st === 'partial') return '<span class="chip" style="color:#ffb000">partial</span>';
    if (st === 'skipped') return '<span class="chip chip-skip">skipped</span>';
    return '<span class="chip chip-skip">' + esc(st || '') + '</span>';
  }

  function actionBadge(action) {
    var a = (action || '').toLowerCase();
    if (a === 'limit_placed') return '<span class="chip chip-action-opened">限价挂单</span>';
    if (a.indexOf('skipped') === 0) return '<span class="chip chip-action-confidence">' + esc(action) + '</span>';
    return '<span class="chip chip-action-other">' + esc(action || '--') + '</span>';
  }

  function shortUTC(iso) {
    if (!iso) return '--';
    try {
      var d = new Date(iso);
      return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
    } catch (e) {
      return esc(iso);
    }
  }

  function parseSignalDetail(raw) {
    if (!raw) return {};
    if (typeof raw === 'object') return raw;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return {};
    }
  }

  function signalBrief(detail) {
    var d = parseSignalDetail(detail);
    if (d.error) return esc(d.error);
    var parts = [];
    if (d.score != null) parts.push('分' + d.score);
    if (d.rsi_1h != null) parts.push('RSI ' + d.rsi_1h);
    if (d.near_20d_low) parts.push('近20D低');
    if (d.near_20d_high) parts.push('近20D高');
    if (d.volume_breakout) parts.push('放量突破');
    if (d.rsi_exhaustion) parts.push('RSI衰竭');
    return parts.length ? esc(parts.join(' · ')) : '<span class="text-on-surface-variant">--</span>';
  }

  function panelHtml(prefix, title, source, teacherLabel) {
    return (
      '<header class="border-b border-outline-variant/10 px-8 py-5 flex items-center justify-between flex-wrap gap-4">' +
      '<div class="min-w-0 flex-1">' +
      '<h1 class="text-2xl font-bold tracking-tight">' + esc(title) + '</h1>' +
      '<p class="text-[11px] text-on-surface-variant mt-1">' +
      teacherLabel + ' · <span class="text-primary">L0/L1 量化扫描</span>（24×1D + 60×1H 技术评分）' +
      ' · 不调用 LLM · 不经开仓/持仓顾问</p>' +
      '<p class="text-[10px] text-on-surface-variant/80 mt-1">' +
      '限价 做多−3% / 做空+3% · 6h 超时 · SL 6% / TP 20% · 持仓 15 天 · 5x · 500U · 仅模拟 · 不受 smart_trader 对冲平仓</p>' +
      '</div>' +
      '<div class="flex items-center gap-3 shrink-0">' +
      '<span id="' + prefix + '-status-chip" class="chip chip-off"><span id="' + prefix + '-status-label">已禁用</span></span>' +
      '<label class="toggle"><input type="checkbox" id="' + prefix + '-toggle" data-source="' + esc(source) + '"><span class="slider"></span></label>' +
      '<button type="button" id="' + prefix + '-run-now" data-source="' + esc(source) + '" class="px-3 py-1.5 rounded-lg bg-surface-container text-xs">立即扫描</button>' +
      '<button type="button" id="' + prefix + '-refresh" data-source="' + esc(source) + '" class="px-3 py-1.5 rounded-lg bg-surface-container text-xs">刷新</button>' +
      '</div></header>' +
      '<section class="px-8 py-6 grid grid-cols-2 md:grid-cols-4 gap-4">' +
      '<div class="bg-surface-container-low rounded-xl p-4"><p class="text-[10px] uppercase text-on-surface-variant mb-1">当前持仓</p>' +
      '<p class="mono text-2xl" id="' + prefix + '-stat-open">--</p></div>' +
      '<div class="bg-surface-container-low rounded-xl p-4"><p class="text-[10px] uppercase text-on-surface-variant mb-1">30天平仓</p>' +
      '<p class="mono text-2xl" id="' + prefix + '-stat-closed">--</p></div>' +
      '<div class="bg-surface-container-low rounded-xl p-4"><p class="text-[10px] uppercase text-on-surface-variant mb-1">上轮 L0/L1 池</p>' +
      '<p class="mono text-2xl" id="' + prefix + '-stat-universe">--</p></div>' +
      '<div class="bg-surface-container-low rounded-xl p-4"><p class="text-[10px] uppercase text-on-surface-variant mb-1">上轮挂单</p>' +
      '<p class="mono text-2xl" id="' + prefix + '-stat-orders">--</p></div>' +
      '</section>' +
      '<section class="px-8 pb-6"><div class="flex items-center justify-between mb-3">' +
      '<h2 class="text-sm font-bold uppercase tracking-widest text-on-surface-variant">运行记录</h2>' +
      '<p class="text-[10px] text-on-surface-variant">点击行查看量化信号明细</p></div>' +
      '<div class="bg-surface-container-low rounded-xl overflow-x-auto"><table class="w-full text-xs">' +
      '<thead class="bg-surface-container-low"><tr class="border-b border-outline-variant/20">' +
      '<th class="px-4 py-3 text-left">ID</th><th class="px-4 py-3 text-left">时间</th>' +
      '<th class="px-4 py-3 text-right">池</th><th class="px-4 py-3 text-right">量化信号</th>' +
      '<th class="px-4 py-3 text-right">挂单</th><th class="px-4 py-3 text-right">耗时</th>' +
      '<th class="px-4 py-3 text-left">状态</th><th class="px-4 py-3 text-left">摘要</th>' +
      '</tr></thead><tbody id="' + prefix + '-runs-tbody"><tr><td colspan="8" class="px-4 py-6 text-center text-on-surface-variant">加载中...</td></tr></tbody></table></div></section>' +
      '<section class="px-8 pb-6 hidden" id="' + prefix + '-verdicts-wrap">' +
      '<h2 class="text-sm font-bold uppercase tracking-widest text-on-surface-variant mb-3">' +
      '量化信号明细 <span class="text-[10px] font-normal normal-case" id="' + prefix + '-verdicts-run-label"></span></h2>' +
      '<div class="bg-surface-container-low rounded-xl overflow-x-auto"><table class="w-full text-xs">' +
      '<thead><tr class="border-b border-outline-variant/20">' +
      '<th class="px-4 py-3 text-left">Symbol</th><th class="px-4 py-3 text-left">方向</th>' +
      '<th class="px-4 py-3 text-right">评分</th><th class="px-4 py-3 text-left">技术摘要</th>' +
      '<th class="px-4 py-3 text-left">处理</th><th class="px-4 py-3 text-left">说明</th>' +
      '</tr></thead><tbody id="' + prefix + '-verdicts-tbody"></tbody></table></div></section>' +
      '<section class="px-8 pb-10"><h2 class="text-sm font-bold uppercase tracking-widest text-on-surface-variant mb-3">当前持仓</h2>' +
      '<div class="bg-surface-container-low rounded-xl overflow-x-auto"><table class="w-full text-xs">' +
      '<thead><tr class="border-b border-outline-variant/20">' +
      '<th class="px-4 py-3 text-left">Symbol</th><th class="px-4 py-3 text-left">方向</th>' +
      '<th class="px-4 py-3 text-right">开仓价</th><th class="px-4 py-3 text-right">浮盈</th>' +
      '<th class="px-4 py-3 text-left">开仓</th><th class="px-4 py-3 text-left">计划平仓</th>' +
      '</tr></thead><tbody id="' + prefix + '-open-tbody"><tr><td colspan="6" class="px-4 py-6 text-center text-on-surface-variant">加载中...</td></tr></tbody></table></div></section>'
    );
  }

  function bindPanel(prefix, source) {
    var toggle = document.getElementById(prefix + '-toggle');
    if (toggle && !toggle._midlineBound) {
      toggle._midlineBound = true;
      toggle.addEventListener('change', function () {
        fetch(API + '/toggle?source=' + encodeURIComponent(source), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: toggle.checked }),
        }).then(function () { loadStatus(prefix, source); });
      });
    }
    var runBtn = document.getElementById(prefix + '-run-now');
    if (runBtn && !runBtn._midlineBound) {
      runBtn._midlineBound = true;
      runBtn.addEventListener('click', function () {
        fetch(API + '/run-now?source=' + encodeURIComponent(source), { method: 'POST' })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (typeof global.showToast === 'function') global.showToast(d.message || '已触发量化扫描', 'info');
          });
      });
    }
    var refBtn = document.getElementById(prefix + '-refresh');
    if (refBtn && !refBtn._midlineBound) {
      refBtn._midlineBound = true;
      refBtn.addEventListener('click', function () { loadAll(prefix, source); });
    }
  }

  function loadStatus(prefix, source) {
    fetch(API + '/status?source=' + encodeURIComponent(source))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var s = d.data || {};
        var enabled = !!s.enabled;
        var chip = document.getElementById(prefix + '-status-chip');
        var label = document.getElementById(prefix + '-status-label');
        var toggle = document.getElementById(prefix + '-toggle');
        if (chip) chip.className = enabled ? 'chip chip-on' : 'chip chip-off';
        if (label) label.textContent = enabled ? '已启用' : '已禁用';
        if (toggle) toggle.checked = enabled;
        var el;
        el = document.getElementById(prefix + '-stat-open');
        if (el) el.textContent = s.open_positions != null ? s.open_positions : '--';
        el = document.getElementById(prefix + '-stat-closed');
        if (el) el.textContent = s.closed_positions_30d != null ? s.closed_positions_30d : '--';
        var lr = s.last_run || {};
        el = document.getElementById(prefix + '-stat-universe');
        if (el) el.textContent = lr.universe_size != null ? lr.universe_size : '--';
        el = document.getElementById(prefix + '-stat-orders');
        if (el) el.textContent = lr.orders_placed != null ? lr.orders_placed : '--';
      })
      .catch(function (e) { console.error('midline status', e); });
  }

  function loadVerdicts(prefix, source, runId) {
    var wrap = document.getElementById(prefix + '-verdicts-wrap');
    var tbody = document.getElementById(prefix + '-verdicts-tbody');
    var label = document.getElementById(prefix + '-verdicts-run-label');
    if (!tbody || !wrap) return;
    wrap.classList.remove('hidden');
    if (label) label.textContent = '(run #' + runId + ')';
    tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-6 text-center text-on-surface-variant">加载中...</td></tr>';
    fetch(API + '/verdicts?source=' + encodeURIComponent(source) + '&run_id=' + runId)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var rows = d.data || [];
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-6 text-center text-on-surface-variant">本轮无量化信号</td></tr>';
          return;
        }
        tbody.innerHTML = rows.map(function (v) {
          return '<tr class="border-b border-outline-variant/10">' +
            '<td class="px-4 py-3 mono">' + esc(v.symbol) + '</td>' +
            '<td class="px-4 py-3">' + dirBadge(v.side) + '</td>' +
            '<td class="px-4 py-3 text-right mono font-bold">' + fmt(v.score, 1) + '</td>' +
            '<td class="px-4 py-3 text-[11px] max-w-[200px]">' + signalBrief(v.signal_detail) + '</td>' +
            '<td class="px-4 py-3">' + actionBadge(v.action_taken) + '</td>' +
            '<td class="px-4 py-3 text-[11px] text-on-surface-variant max-w-xs truncate" title="' + esc(v.skip_reason || '') + '">' +
            esc(v.skip_reason || (v.order_id ? 'order #' + v.order_id : '')) + '</td></tr>';
        }).join('');
      })
      .catch(function () {
        tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-4 text-center text-error">加载失败</td></tr>';
      });
  }

  function loadRuns(prefix, source) {
    fetch(API + '/runs?source=' + encodeURIComponent(source) + '&limit=15')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var rows = d.data || [];
        var tbody = document.getElementById(prefix + '-runs-tbody');
        if (!tbody) return;
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-6 text-center text-on-surface-variant">暂无记录</td></tr>';
          return;
        }
        tbody.innerHTML = rows.map(function (r) {
          return '<tr class="border-b border-outline-variant/10 hover:bg-surface-container-high/50 cursor-pointer transition-colors" ' +
            'data-run-id="' + r.id + '" title="点击查看量化信号">' +
            '<td class="px-4 py-3 mono">' + r.id + '</td>' +
            '<td class="px-4 py-3 mono text-[11px]">' + shortUTC(r.asof_utc) + '</td>' +
            '<td class="px-4 py-3 text-right mono">' + (r.universe_size || 0) + '</td>' +
            '<td class="px-4 py-3 text-right mono">' + (r.signals_found || 0) + '</td>' +
            '<td class="px-4 py-3 text-right mono">' + (r.orders_placed || 0) + '</td>' +
            '<td class="px-4 py-3 text-right mono">' + fmt(r.elapsed_s, 1) + 's</td>' +
            '<td class="px-4 py-3">' + statusBadge(r.status) + '</td>' +
            '<td class="px-4 py-3 text-[11px] max-w-xs truncate" title="' + esc(r.summary_short || r.error_msg || '') + '">' +
            esc(r.summary_short || r.error_msg || '') + '</td></tr>';
        }).join('');
        tbody.querySelectorAll('tr[data-run-id]').forEach(function (tr) {
          tr.addEventListener('click', function () {
            var rid = parseInt(tr.getAttribute('data-run-id'), 10);
            if (rid) loadVerdicts(prefix, source, rid);
          });
        });
        if (rows[0] && rows[0].id) {
          loadVerdicts(prefix, source, rows[0].id);
        }
      });
  }

  function loadOpen(prefix, source) {
    fetch(API + '/positions?source=' + encodeURIComponent(source) + '&status=open')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var rows = d.data || [];
        var tbody = document.getElementById(prefix + '-open-tbody');
        if (!tbody) return;
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-6 text-center text-on-surface-variant">无持仓</td></tr>';
          return;
        }
        tbody.innerHTML = rows.map(function (p) {
          var pnl = p.unrealized_pnl;
          return '<tr class="border-b border-outline-variant/10">' +
            '<td class="px-4 py-3 mono">' + esc(p.symbol) + '</td>' +
            '<td class="px-4 py-3">' + dirBadge(p.position_side) + '</td>' +
            '<td class="px-4 py-3 text-right mono">' + fmt(p.entry_price, 6) + '</td>' +
            '<td class="px-4 py-3 text-right mono ' + pCls(pnl) + '">' + fmtSigned(pnl) + '</td>' +
            '<td class="px-4 py-3 mono text-[11px]">' + shortUTC(p.open_time) + '</td>' +
            '<td class="px-4 py-3 mono text-[11px]">' + shortUTC(p.planned_close_time) + '</td></tr>';
        }).join('');
      });
  }

  function loadAll(prefix, source) {
    loadStatus(prefix, source);
    loadRuns(prefix, source);
    loadOpen(prefix, source);
  }

  function init(cfg) {
    var teacher = (cfg && cfg.teacher) || 'gemini';
    var teacherLabel = teacher === 'gemini' ? 'Gemini 分轨' : 'DeepSeek 分轨';
    var panels = [
      {
        tabId: 'midline-long',
        prefix: teacher === 'gemini' ? 'gml' : 'dml',
        title: '中线做多（量化）',
        source: teacher + '_midline_long',
      },
      {
        tabId: 'midline-short',
        prefix: teacher === 'gemini' ? 'gms' : 'dms',
        title: '中线做空（量化）',
        source: teacher + '_midline_short',
      },
    ];
    panels.forEach(function (p) {
      var el = document.getElementById('tab-' + p.tabId);
      if (!el) return;
      if (el.dataset.midlineRendered !== PANEL_VERSION) {
        el.innerHTML = panelHtml(p.prefix, p.title, p.source, teacherLabel);
        el.dataset.midlineRendered = PANEL_VERSION;
      }
      bindPanel(p.prefix, p.source);
      el._midlinePrefix = p.prefix;
      el._midlineSource = p.source;
    });
    global._midlinePanels = panels;
  }

  function onTabShow(tabId) {
    var panels = global._midlinePanels || [];
    panels.forEach(function (p) {
      if (p.tabId === tabId) loadAll(p.prefix, p.source);
    });
  }

  global.MidlineSwingTab = {
    init: init,
    onTabShow: onTabShow,
    loadAll: loadAll,
    loadVerdicts: loadVerdicts,
  };
})(window);
