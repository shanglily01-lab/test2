/**
 * Big4 综合行情 Tab — Gemini / DeepSeek 探索页共用
 * 用法: Big4AnalysisTab.init({ api: '/api/gemini-big4-analysis' })
 */
(function (global) {
  function apiBase(cfg) {
    return cfg.api || '/api/gemini-big4-analysis';
  }

  function big4Badge(label) {
    var l = (label || '').toLowerCase();
    if (l === 'bullish') return '<span class="chip chip-long">看多</span>';
    if (l === 'bearish') return '<span class="chip chip-short">看空</span>';
    if (l === 'sideways') return '<span class="chip chip-action-big4">震荡</span>';
    if (l === 'mixed') return '<span class="chip chip-action-big4">分化</span>';
    if (l === 'neutral') return '<span class="chip chip-skip">中性</span>';
    return '<span class="chip chip-off">' + (label || '--') + '</span>';
  }

  function loadStatus(cfg) {
    fetch(apiBase(cfg) + '/status')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) return;
        var s = d.data || {};
        var toggle = document.getElementById('b4-enable-toggle');
        if (toggle) toggle.checked = !!s.enabled;
        var chip = document.getElementById('b4-status-chip');
        var label = document.getElementById('b4-status-label');
        if (chip && label) {
          if (s.enabled) {
            chip.className = 'chip chip-on';
            chip.querySelector('span.material-symbols-outlined').textContent = 'power';
            label.textContent = '已启用';
          } else {
            chip.className = 'chip chip-off';
            chip.querySelector('span.material-symbols-outlined').textContent = 'power_off';
            label.textContent = '已禁用';
          }
        }
        var runsEl = document.getElementById('b4-stat-runs');
        if (runsEl) runsEl.textContent = s.runs_24h != null ? s.runs_24h : '--';
        var lr = s.last_run || {};
        var labelEl = document.getElementById('b4-stat-label');
        if (labelEl) {
          labelEl.innerHTML = lr.overall_label ? big4Badge(lr.overall_label) : '<span class="text-on-surface-variant">--</span>';
        }
        var scoreEl = document.getElementById('b4-stat-score');
        if (scoreEl) {
          scoreEl.textContent = lr.overall_score != null ? (lr.overall_score >= 0 ? '+' : '') + Number(lr.overall_score).toFixed(2) : '--';
        }
        var quantEl = document.getElementById('b4-stat-quant');
        if (quantEl) quantEl.textContent = lr.big4_quant_signal || '--';
        var lastEl = document.getElementById('b4-stat-lastrun');
        if (lastEl) {
          lastEl.innerHTML = lr.asof_utc
            ? '<span class="text-on-surface-variant text-[10px]">上轮: ' + shortUTC(lr.asof_utc) + '</span>'
            : '';
        }
      })
      .catch(function (e) { console.error('big4 status', e); });
  }

  function onToggle(cfg, checked) {
    fetch(apiBase(cfg) + '/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: checked })
    })
      .then(function (r) { return r.json(); })
      .then(function () { setTimeout(function () { loadStatus(cfg); }, 200); })
      .catch(function (e) { if (typeof exploreNotify === 'function') exploreNotify('切换失败: ' + e, 'error'); });
  }

  var detailCache = {};

  function loadRuns(cfg) {
    fetch(apiBase(cfg) + '/runs?limit=20')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var rows = d.data || [];
        var tbody = document.getElementById('b4-runs-tbody');
        if (!tbody) return;
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="9" class="px-4 py-8 text-center text-on-surface-variant text-xs">暂无记录</td></tr>';
          return;
        }
        tbody.innerHTML = rows.map(function (r) {
          return '<tr class="border-b border-outline-variant/10 hover:bg-surface-container/40 cursor-pointer" onclick="Big4AnalysisTab.toggleDetail(' + r.id + ')">' +
            '<td class="px-4 py-3 mono text-on-surface-variant">' + r.id + '</td>' +
            '<td class="px-4 py-3 mono text-[11px]">' + shortUTC(r.asof_utc) + '</td>' +
            '<td class="px-4 py-3">' + big4Badge(r.overall_label) + '</td>' +
            '<td class="px-4 py-3 text-right mono">' + (r.overall_score != null ? Number(r.overall_score).toFixed(2) : '--') + '</td>' +
            '<td class="px-4 py-3 text-on-surface-variant text-[11px]">' + escapeHtml(r.big4_quant_signal || '--') + '</td>' +
            '<td class="px-4 py-3 text-right mono text-on-surface-variant text-[11px]">' + (typeof fmt === 'function' ? fmt(r.elapsed_s, 1) : r.elapsed_s) + 's</td>' +
            '<td class="px-4 py-3">' + (typeof statusBadge === 'function' ? statusBadge(r.status) : r.status) + '</td>' +
            '<td class="px-4 py-3 text-on-surface-variant text-[11px] max-w-md truncate" title="' + escapeHtml(r.direction_verdict || r.analysis_summary_short || '') + '">' +
            escapeHtml(r.direction_verdict || r.analysis_summary_short || '') + '</td>' +
            '<td class="px-4 py-3 text-on-surface-variant text-[11px]">' + escapeHtml(r.triggered_by || '') + '</td>' +
            '</tr>' +
            '<tr id="b4-detail-row-' + r.id + '" class="hidden"><td colspan="9" class="px-6 py-4" id="b4-detail-cell-' + r.id + '"></td></tr>';
        }).join('');
      });
  }

  function renderDetail(row) {
    var html = '';
    if (row.direction_verdict) {
      html += '<p class="text-xs font-bold text-on-surface-variant mb-1">方向判断</p>' +
        '<p class="text-sm text-on-surface mb-4 whitespace-pre-wrap">' + escapeHtml(row.direction_verdict) + '</p>';
    }
    if (row.analysis_summary_zh) {
      html += '<p class="text-xs font-bold text-on-surface-variant mb-1">综合分析</p>' +
        '<p class="text-sm text-on-surface mb-4 whitespace-pre-wrap">' + escapeHtml(row.analysis_summary_zh) + '</p>';
    }
    if (row.per_coin_json) {
      try {
        var coins = typeof row.per_coin_json === 'string' ? JSON.parse(row.per_coin_json) : row.per_coin_json;
        html += '<p class="text-xs font-bold text-on-surface-variant mb-2">分币解读</p><div class="grid grid-cols-2 gap-3">';
        ['BTC', 'ETH', 'BNB', 'SOL'].forEach(function (sym) {
          var c = coins[sym] || {};
          html += '<div class="bg-surface-container rounded-lg p-3 text-xs">' +
            '<div class="flex items-center justify-between mb-1"><span class="font-bold mono">' + sym + '</span>' +
            big4Badge(c.label) + '</div>' +
            '<p class="text-on-surface-variant whitespace-pre-wrap">' + escapeHtml(c.note_zh || '--') + '</p></div>';
        });
        html += '</div>';
      } catch (e) { /* ignore */ }
    }
    return html || '<p class="text-on-surface-variant text-xs">无详情</p>';
  }

  function toggleDetail(cfg, runId) {
    var row = document.getElementById('b4-detail-row-' + runId);
    if (!row) return;
    if (!row.classList.contains('hidden')) {
      row.classList.add('hidden');
      return;
    }
    document.querySelectorAll('[id^="b4-detail-row-"]').forEach(function (r) {
      if (r !== row) r.classList.add('hidden');
    });
    var cell = document.getElementById('b4-detail-cell-' + runId);
    if (detailCache[runId]) {
      cell.innerHTML = detailCache[runId];
      row.classList.remove('hidden');
      return;
    }
    cell.innerHTML = '<p class="text-xs text-on-surface-variant">加载中...</p>';
    row.classList.remove('hidden');
    fetch(apiBase(cfg) + '/detail?run_id=' + runId)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var html = renderDetail(d.data || {});
        detailCache[runId] = html;
        cell.innerHTML = html;
      })
      .catch(function (e) {
        cell.innerHTML = '<p class="text-on-surface-variant text-xs">加载失败: ' + e + '</p>';
      });
  }

  function loadLatest(cfg) {
    fetch(apiBase(cfg) + '/status')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var el = document.getElementById('b4-latest-result');
        if (!el) return;
        var lr = (d.data || {}).last_run;
        if (!lr || lr.status !== 'ok') {
          el.innerHTML = '<div class="text-center text-on-surface-variant text-xs py-8">暂无成功分析</div>';
          return;
        }
        fetch(apiBase(cfg) + '/detail?run_id=' + lr.id)
          .then(function (r2) { return r2.json(); })
          .then(function (d2) {
            el.innerHTML = renderDetail(d2.data || {});
          });
      });
  }

  function loadAll(cfg) {
    loadStatus(cfg);
    loadLatest(cfg);
    loadRuns(cfg);
  }

  function init(cfg) {
    global._big4AnalysisCfg = cfg;
    var toggle = document.getElementById('b4-enable-toggle');
    if (toggle) {
      toggle.onchange = function () { onToggle(cfg, toggle.checked); };
    }
    var refreshBtn = document.getElementById('b4-refresh-btn');
    if (refreshBtn) refreshBtn.onclick = function () { loadAll(cfg); };
    var runBtn = document.getElementById('b4-run-now-btn');
    if (runBtn) {
      runBtn.onclick = function () {
        fetch(apiBase(cfg) + '/run-now', { method: 'POST' })
          .then(function (r) { return r.json(); })
          .then(function () {
            if (typeof exploreNotify === 'function') exploreNotify('已启动后台分析', 'info');
            setTimeout(function () { loadAll(cfg); }, 3000);
          });
      };
    }
  }

  global.Big4AnalysisTab = {
    init: init,
    loadAll: loadAll,
    toggleDetail: function (runId) {
      toggleDetail(global._big4AnalysisCfg || { api: '/api/gemini-big4-analysis' }, runId);
    }
  };
})(window);
