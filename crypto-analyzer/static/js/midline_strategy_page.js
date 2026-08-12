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
  function dimsText(dims) {
    if (!dims) return '--';
    var keys = ['cycle', 'm3', 'm1', 'd7', 'd1'];
    return keys.map(function (k) {
      var d = dims[k] || {};
      return (d.label || k) + ':' + (d.side || 'FLAT');
    }).join(' / ');
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
      body.innerHTML = '<tr><td colspan="6" class="px-3 py-8 text-center text-on-surface-variant">暂无可展示机会</td></tr>';
      return;
    }
    rows.forEach(function (r) {
      var sd = r.signal_detail || {};
      var setup = sd.setup || (sd.entry || {}).setup || r.skip_reason || '--';
      var dims = sd.trend_dimensions || {};
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="px-3 py-2 mono font-semibold">' + (r.symbol || '') + '</td>' +
        '<td class="px-3 py-2">' + chip(r.side) + '</td>' +
        '<td class="px-3 py-2 mono">' + (r.action_taken || '') + '</td>' +
        '<td class="px-3 py-2 mono">' + fmt(r.score) + '</td>' +
        '<td class="px-3 py-2 text-on-surface-variant">' + setup + '</td>' +
        '<td class="px-3 py-2 text-on-surface-variant max-w-[520px]">' + dimsText(dims) + '</td>';
      body.appendChild(tr);
    });
  }
  function renderPositions(rows) {
    var box = $('positions-list');
    box.innerHTML = '';
    if (!rows.length) {
      box.innerHTML = '<p class="text-on-surface-variant">暂无中线持仓</p>';
      return;
    }
    rows.forEach(function (p) {
      var div = document.createElement('div');
      div.className = 'bg-surface-container-low rounded-lg border border-outline-variant/10 px-3 py-2';
      div.innerHTML =
        '<div class="flex items-center justify-between"><span class="mono font-semibold">' + p.symbol + '</span>' +
        chip(p.position_side) + '</div>' +
        '<div class="mt-1 mono text-on-surface-variant">entry ' + fmt(p.entry_price) +
        ' · pnl ' + fmt(p.unrealized_pnl, 'U') + ' · ' + time(p.open_time) + '</div>';
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
        $('last-run').textContent = runs.length ? runs.map(function (r) {
          return r.source + ' #' + r.id + ' ' + time(r.asof_utc);
        }).join(' | ') : '--';
        renderTrend(d);
        renderOpportunities(d.opportunities || []);
        renderPositions(d.positions || []);
        renderUniverse(d.universe || []);
      })
      .catch(function (e) {
        console.error(e);
        $('opportunity-body').innerHTML = '<tr><td colspan="6" class="px-3 py-8 text-center text-error">' + String(e) + '</td></tr>';
      });
  }
  function runNow() {
    $('btn-run').disabled = true;
    fetch(API + '/run-now', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function () { setTimeout(loadDashboard, 2500); })
      .finally(function () { $('btn-run').disabled = false; });
  }
  $('btn-refresh').onclick = loadDashboard;
  $('btn-run').onclick = runNow;
  loadDashboard();
  setInterval(loadDashboard, 60000);
})();
