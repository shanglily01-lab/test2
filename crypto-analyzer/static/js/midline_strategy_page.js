(function () {
  var API = '/api/midline-swing';
  function $(id) { return document.getElementById(id); }
  function cls(side) {
    side = String(side || 'FLAT').toUpperCase();
    return side === 'LONG' ? 'long' : side === 'SHORT' ? 'short' : 'flat';
  }
  function chip(side) {
    side = String(side || 'FLAT').toUpperCase();
    return '<span class="chip ' + cls(side) + '">' + side + '</span>';
  }
  function fmt(v, suffix) {
    if (v === null || v === undefined || v === '') return '--';
    var n = Number(v);
    if (Number.isFinite(n)) return n.toFixed(Math.abs(n) >= 10 ? 1 : 2) + (suffix || '');
    return String(v);
  }
  function time(v) {
    return v ? String(v).replace('T', ' ').slice(0, 19) : '--';
  }
  function setRunStatus(text, isError) {
    var el = $('last-run');
    if (!el) return;
    el.textContent = text || '--';
    el.classList.toggle('text-error', !!isError);
    el.classList.toggle('text-primary', !isError && !!text && text !== '--');
  }
  function dimsText(dims) {
    if (!dims) return '--';
    var keys = ['cycle', 'm3', 'm1', 'd7', 'd1', 'future_4h'];
    return keys.map(function (k) {
      var d = dims[k] || {};
      return (d.label || k) + ':' + (d.side || 'FLAT');
    }).join(' / ');
  }
  function setupLabel(setup) {
    var labels = {
      fresh_breakdown_short: '放量跌破支撑',
      breakdown_short: '跌破支撑',
      fresh_breakout_long: '放量突破阻力',
      breakout_long: '突破阻力',
      brain_A1_trend_continuation_long: 'BRAIN A1 趋势续涨',
      brain_A2_failed_rebound_short: 'BRAIN A2 反抽转跌',
      brain_C1_breakdown_short: 'BRAIN C1 向下破位',
      brain_C3_breakout_long: 'BRAIN C3 向上突破'
    };
    return labels[setup] || setup || '--';
  }
  function setupText(sd, fallback) {
    var entry = sd.entry || {};
    var setup = sd.setup || entry.setup || fallback || '--';
    var bits = [setupLabel(setup)];
    if (entry.break_pct !== undefined) bits.push('破位 ' + fmt(entry.break_pct, '%'));
    if (entry.playbook) bits.push(entry.playbook + ' edge ' + fmt(entry.edge_score));
    if (entry.phase_range_pct !== undefined) bits.push('阶段区间 ' + fmt(entry.phase_range_pct, '%'));
    if (entry.h1_side || entry.m15_side) bits.push('1H/' + (entry.h1_side || '--') + ' 15M/' + (entry.m15_side || '--'));
    if (entry.change_1h_pct !== undefined) bits.push('1H ' + fmt(entry.change_1h_pct, '%'));
    if (entry.change_4h_pct !== undefined) bits.push('4H ' + fmt(entry.change_4h_pct, '%'));
    if (entry.vol_ratio !== undefined) bits.push('量比 ' + fmt(entry.vol_ratio));
    if (entry.strong_break) bits.push('强破位');
    if (entry.volume_ok === false) bits.push('量能一般');
    if (entry.global_regime) bits.push(entry.global_regime);
    return bits.join('<br><span class="mono text-[10px] text-on-surface-variant">') + (bits.length > 1 ? '</span>' : '');
  }
  function orderText(order) {
    if (!order) return '--';
    return [
      '@' + fmt(order.price),
      'SL ' + fmt(order.stop_loss_price),
      'TP ' + fmt(order.take_profit_price),
      (order.max_hold_minutes ? '持仓 ' + fmt(Number(order.max_hold_minutes) / 60, 'h') : ''),
      (order.timeout_minutes ? '有效 ' + order.timeout_minutes + 'm' : '')
    ].filter(Boolean).join(' · ');
  }
  function futureText(f) {
    if (!f || !f.side) return '--';
    return chip(f.side) + '<div class="mt-1 mono text-[10px] text-on-surface-variant">' +
      '4h ' + fmt(f.change_4h_pct, '%') + ' · 8h ' + fmt(f.change_8h_pct, '%') + '</div>';
  }
  function renderTrend(data) {
    var grid = $('trend-grid');
    grid.innerHTML = '';
    ((data.trend || {}).dimensions || []).forEach(function (d) {
      var coins = (d.coins || []).map(function (c) {
        return '<div class="flex items-center justify-between gap-2 text-[11px] mono text-on-surface-variant">' +
          '<span>' + c.symbol + '</span><span class="' + cls(c.side) + ' px-1.5 rounded">' + c.side + '</span>' +
          '<span>' + fmt(c.change_pct, '%') + '</span></div>';
      }).join('');
      var div = document.createElement('div');
      div.className = 'bg-surface-container-low rounded-lg border border-outline-variant/10 p-3 min-h-[170px]';
      div.innerHTML =
        '<div class="flex items-center justify-between mb-2">' +
        '<h3 class="text-sm font-semibold">' + d.label + '</h3>' + chip(d.side) + '</div>' +
        '<div class="space-y-1">' + coins + '</div>';
      grid.appendChild(div);
    });
  }
  function renderOpportunities(rows) {
    var body = $('opportunity-body');
    body.innerHTML = '';
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="8" class="px-3 py-8 text-center text-on-surface-variant">暂无可展示破位机会</td></tr>';
      return;
    }
    rows.forEach(function (r) {
      var sd = r.signal_detail || {};
      var dims = sd.trend_dimensions || {};
      var f4h = sd.future_4h || {};
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="px-3 py-2 mono font-semibold">' + (r.symbol || '') + '</td>' +
        '<td class="px-3 py-2">' + chip(r.side) + '</td>' +
        '<td class="px-3 py-2 mono">' + (r.action_taken || '') + '</td>' +
        '<td class="px-3 py-2">' + futureText(f4h) + '</td>' +
        '<td class="px-3 py-2 mono">' + fmt(r.score) + '</td>' +
        '<td class="px-3 py-2 text-on-surface-variant">' + setupText(sd, r.skip_reason) + '</td>' +
        '<td class="px-3 py-2 text-on-surface-variant max-w-[300px]">' + orderText(r.order) + '</td>' +
        '<td class="px-3 py-2 text-on-surface-variant max-w-[520px]">' + dimsText(dims) + '</td>';
      body.appendChild(tr);
    });
  }
  function renderPositions(rows) {
    var box = $('positions-list');
    box.innerHTML = '';
    if (!rows.length) {
      box.innerHTML = '<p class="text-on-surface-variant">暂无持仓</p>';
      return;
    }
    rows.forEach(function (p) {
      var div = document.createElement('div');
      div.className = 'bg-surface-container-low rounded-lg border border-outline-variant/10 px-3 py-2';
      div.innerHTML =
        '<div class="flex items-center justify-between"><span class="mono font-semibold">' + p.symbol + '</span>' +
        chip(p.position_side) + '</div>' +
        '<div class="mt-1 mono text-on-surface-variant">entry ' + fmt(p.entry_price) +
        ' · pnl ' + fmt(p.unrealized_pnl, 'U') + '</div>' +
        '<div class="mt-1 mono text-on-surface-variant">SL ' + fmt(p.stop_loss_price) +
        ' · TP ' + fmt(p.take_profit_price) + '</div>' +
        '<div class="mt-1 mono text-on-surface-variant">open ' + time(p.open_time) +
        ' · hold until ' + time(p.planned_close_time) + '</div>';
      box.appendChild(div);
    });
  }
  function renderOrders(rows) {
    var box = $('orders-list');
    box.innerHTML = '';
    if (!rows.length) {
      box.innerHTML = '<p class="text-on-surface-variant">暂无订单</p>';
      return;
    }
    rows.slice(0, 12).forEach(function (o) {
      var side = String(o.side || '').replace('OPEN_', '');
      var div = document.createElement('div');
      div.className = 'bg-surface-container-low rounded-lg border border-outline-variant/10 px-3 py-2';
      div.innerHTML =
        '<div class="flex items-center justify-between gap-2"><span class="mono font-semibold">' + (o.symbol || '') + '</span>' +
        chip(side) + '</div>' +
        '<div class="mt-1 mono text-on-surface-variant">' + (o.status || '--') +
        ' · @' + fmt(o.price) + ' · qty ' + fmt(o.quantity) + '</div>' +
        '<div class="mt-1 mono text-on-surface-variant">SL ' + fmt(o.stop_loss_price) +
        ' · TP ' + fmt(o.take_profit_price) + '</div>' +
        '<div class="mt-1 mono text-on-surface-variant">created ' + time(o.created_at) +
        ' · hold ' + (o.max_hold_minutes ? fmt(Number(o.max_hold_minutes) / 60, 'h') : '--') + '</div>';
      box.appendChild(div);
    });
  }
  function renderUniverse(rows) {
    var box = $('universe-list');
    box.innerHTML = '';
    rows.forEach(function (s) {
      var span = document.createElement('span');
      span.className = 'px-2 py-1 rounded bg-surface-container-low border border-outline-variant/10 text-on-surface-variant';
      span.textContent = s;
      box.appendChild(span);
    });
  }
  function loadDashboard() {
    return fetch(API + '/dashboard')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.success) throw new Error(j.detail || 'dashboard failed');
        var d = j.data || {};
        $('stat-bias').innerHTML = chip((d.trend || {}).bias);
        $('stat-interval').textContent = (d.scan_interval_minutes || 15) + 'M';
        $('stat-universe').textContent = d.universe_size || 0;
        $('stat-opp').textContent = (d.opportunities || []).length;
        $('stat-pos').textContent = (d.positions || []).length;
        var runs = d.latest_runs || [];
        setRunStatus(runs.length ? runs.map(function (r) {
          return r.source + ' #' + r.id + ' ' + time(r.asof_utc);
        }).join(' | ') : '--');
        renderTrend(d);
        renderOpportunities(d.opportunities || []);
        renderPositions(d.positions || []);
        renderOrders(d.orders || []);
        renderUniverse(d.universe || []);
      })
      .catch(function (e) {
        console.error(e);
        setRunStatus('刷新失败: ' + String(e), true);
        $('opportunity-body').innerHTML = '<tr><td colspan="8" class="px-3 py-8 text-center text-error">' + String(e) + '</td></tr>';
      });
  }
  function runNow() {
    var btn = $('btn-run');
    btn.disabled = true;
    btn.classList.add('opacity-70', 'cursor-wait');
    setRunStatus('已触发扫描，后台分析中...', false);
    fetch(API + '/run-now', { method: 'POST' })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.body.success) throw new Error((res.body || {}).detail || (res.body || {}).message || 'run-now failed');
        setRunStatus(res.body.message || '已触发扫描，后台分析中...', false);
        [2500, 7000, 15000, 30000, 60000].forEach(function (ms) {
          setTimeout(loadDashboard, ms);
        });
      })
      .catch(function (e) {
        console.error(e);
        setRunStatus('触发失败: ' + String(e), true);
      })
      .finally(function () {
        btn.disabled = false;
        btn.classList.remove('opacity-70', 'cursor-wait');
      });
  }
  $('btn-refresh').onclick = loadDashboard;
  $('btn-run').onclick = runNow;
  loadDashboard();
  setInterval(loadDashboard, 60000);
})();
