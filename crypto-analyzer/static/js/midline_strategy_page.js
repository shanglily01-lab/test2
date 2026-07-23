/**
 * 中线策略页 — 机会分析 / 参数 / 持仓
 */
(function () {
  var API = '/api/midline-swing';

  function $(id) { return document.getElementById(id); }

  function toast(msg, ok) {
    var el = $('params-msg');
    if (!el) return;
    el.textContent = msg;
    el.style.color = ok ? '#49f4c8' : '#ff716c';
  }

  function fmtTime(v) {
    if (!v) return '--';
    return String(v).replace('T', ' ').slice(0, 19);
  }

  function loadOverview() {
    return fetch(API + '/overview')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.success) throw new Error(j.detail || 'overview failed');
        var d = j.data;
        var long = (d.sources || {}).midline_long || {};
        var short = (d.sources || {}).midline_short || {};
        $('tog-long').checked = !!long.enabled;
        $('tog-short').checked = !!short.enabled;
        $('stat-long-open').textContent = '持仓 ' + (long.open_positions || 0);
        $('stat-short-open').textContent = '持仓 ' + (short.open_positions || 0);
        var p = d.params || {};
        $('p-interval').textContent = (p.interval_hours != null ? p.interval_hours : '--');
        $('p-offset').textContent = '多−' + p.limit_long_offset_pct + '% / 空+' + p.limit_short_offset_pct + '%';
        $('p-sltp').textContent = 'SL ' + d.sl_pct + '% / TP ' + d.tp_pct + '%';
        $('p-hold').textContent = d.hold_hours + 'h / ' + d.leverage + 'x / ' + d.margin_usd + 'U';
        $('inp-interval').value = p.interval_hours;
        $('inp-long-off').value = p.limit_long_offset_pct;
        $('inp-short-off').value = p.limit_short_offset_pct;
      })
      .catch(function (e) { console.error(e); toast(String(e), false); });
  }

  function loadRuns() {
    return fetch(API + '/runs?limit=30')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = (j.data || []);
        var sel = $('sel-run');
        var list = $('runs-list');
        sel.innerHTML = '';
        list.innerHTML = '';
        rows.forEach(function (r, i) {
          var opt = document.createElement('option');
          opt.value = r.id;
          opt.textContent = '#' + r.id + ' ' + r.source + ' ' + fmtTime(r.asof_utc) + ' ' + (r.status || '');
          sel.appendChild(opt);
          var div = document.createElement('div');
          div.className = 'bg-surface-container-low rounded-lg px-3 py-2 cursor-pointer hover:bg-surface-container-highest';
          div.textContent = '#' + r.id + ' ' + r.source + ' | 池' + r.universe_size + ' 信号' + r.signals_found + ' 挂单' + r.orders_placed + ' | ' + (r.summary_short || '');
          div.onclick = function () { sel.value = r.id; loadVerdicts(); };
          list.appendChild(div);
          if (i === 0) sel.value = r.id;
        });
        if (rows.length) loadVerdicts();
      });
  }

  function loadVerdicts() {
    var runId = $('sel-run').value;
    if (!runId) return;
    var only = $('chk-passed-only').checked;
    var url = API + '/verdicts?run_id=' + runId + '&limit=400';
    if (only) url += '&only_passed=true';
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = j.data || [];
        $('run-summary').textContent = 'run #' + runId + ' · ' + rows.length + ' 条';
        var tb = $('verdict-body');
        tb.innerHTML = '';
        rows.forEach(function (v) {
          var tr = document.createElement('tr');
          var chip = v.side === 'LONG' ? 'chip-long' : 'chip-short';
          var detail = '';
          try {
            var sd = typeof v.signal_detail === 'string' ? JSON.parse(v.signal_detail) : (v.signal_detail || {});
            if (sd.change_30d_pct != null) detail += '30d ' + sd.change_30d_pct + '% ';
            if (sd.rsi_1d != null) detail += 'RSI1d ' + sd.rsi_1d + ' ';
            if (sd.range_pos != null) detail += 'pos ' + sd.range_pos + ' ';
            if (v.skip_reason) detail += v.skip_reason;
            if (!detail && sd.reason) detail = sd.reason;
          } catch (e) {
            detail = v.skip_reason || '';
          }
          tr.innerHTML =
            '<td class="px-3 py-2 mono">' + (v.symbol || '') + '</td>' +
            '<td class="px-3 py-2"><span class="chip ' + chip + '">' + v.side + '</span></td>' +
            '<td class="px-3 py-2 mono">' + (v.action_taken || '') + '</td>' +
            '<td class="px-3 py-2 mono">' + (v.score != null ? v.score : '') + '</td>' +
            '<td class="px-3 py-2 text-on-surface-variant">' + detail + '</td>';
          tb.appendChild(tr);
        });
      });
  }

  function loadPositions() {
    fetch(API + '/positions?status=open&limit=100')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var el = $('pos-open');
        el.innerHTML = '';
        (j.data || []).forEach(function (p) {
          var div = document.createElement('div');
          div.className = 'bg-surface-container-low rounded-lg px-3 py-2';
          div.innerHTML =
            '<span class="mono font-medium">' + p.symbol + '</span> ' +
            '<span class="chip ' + (p.position_side === 'LONG' ? 'chip-long' : 'chip-short') + '">' + p.position_side + '</span> ' +
            '<span class="text-on-surface-variant">' + (p.source || '') + '</span><br/>' +
            '<span class="mono text-on-surface-variant">entry ' + p.entry_price + ' · margin ' + p.margin + '</span>';
          el.appendChild(div);
        });
        if (!(j.data || []).length) el.innerHTML = '<p class="text-on-surface-variant">无持仓</p>';
      });
  }

  function bind() {
    $('btn-refresh').onclick = function () {
      loadOverview(); loadRuns(); loadPositions();
    };
    $('btn-run-all').onclick = function () {
      fetch(API + '/run-now', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          toast(j.message || '已触发', true);
          setTimeout(function () { loadRuns(); loadOverview(); }, 3000);
        });
    };
    $('btn-save-params').onclick = function () {
      var body = {
        interval_hours: Number($('inp-interval').value),
        limit_long_offset_pct: Number($('inp-long-off').value),
        limit_short_offset_pct: Number($('inp-short-off').value),
      };
      fetch(API + '/params', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.success) throw new Error(j.detail || 'save failed');
          toast('参数已保存', true);
          loadOverview();
        })
        .catch(function (e) { toast(String(e), false); });
    };
    function bindToggle(id, source) {
      $(id).onchange = function () {
        var enabled = $(id).checked;
        fetch(API + '/toggle?source=' + source, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: enabled }),
        }).then(function () { toast(source + (enabled ? ' 已开启' : ' 已关闭'), true); });
      };
    }
    bindToggle('tog-long', 'midline_long');
    bindToggle('tog-short', 'midline_short');
    $('sel-run').onchange = loadVerdicts;
    $('chk-passed-only').onchange = loadVerdicts;
  }

  bind();
  loadOverview();
  loadRuns();
  loadPositions();
})();
