/**
 * 超级大脑策略页 — 概览 / 开关 / 持仓 / 手动跑一轮
 */
(function () {
  var API = '/api/brain-swing';

  function $(id) { return document.getElementById(id); }

  function toast(msg, ok) {
    var el = $('status-msg');
    if (!el) return;
    el.textContent = msg;
    el.style.color = ok ? '#49f4c8' : '#ff716c';
  }

  function fmtTime(v) {
    if (!v) return '--';
    return String(v).replace('T', ' ').slice(0, 19);
  }

  function fmtNum(v, digits) {
    if (v == null || v === '') return '--';
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    return n.toFixed(digits == null ? 4 : digits);
  }

  function sideChip(side) {
    var s = String(side || '').toUpperCase();
    if (s === 'LONG') return '<span class="chip chip-long">LONG</span>';
    if (s === 'SHORT') return '<span class="chip chip-short">SHORT</span>';
    return '<span class="chip chip-off">' + (s || '--') + '</span>';
  }

  function loadOverview() {
    return fetch(API + '/overview')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.success) throw new Error(j.detail || 'overview failed');
        var d = j.data || {};
        var p = d.params || {};
        var b4 = d.big4 || {};
        $('tog-enabled').checked = !!d.enabled;
        $('stat-source').textContent = d.source || 'brain_swing';
        $('p-interval').textContent = p.scan_interval_hours != null ? p.scan_interval_hours : '--';
        $('p-sltp').textContent = 'SL ' + p.sl_pct + '% / TP ' + p.tp_pct + '%';
        $('p-hold').textContent = p.hold_hours + 'h / ' + p.leverage + 'x / ' + p.margin_usd + 'U';
        $('p-counts').textContent = '仓 ' + (d.open_positions || 0) + ' · 挂 ' + (d.pending_limits || 0);
        if (b4.ok) {
          $('p-big4').textContent = '可交易';
          $('p-big4').style.color = '#49f4c8';
        } else {
          $('p-big4').textContent = '疲软';
          $('p-big4').style.color = '#ff716c';
        }
        $('p-big4-bias').textContent = 'bias ' + (b4.bias || 'FLAT') + (b4.reason ? ' · ' + b4.reason : '');
        toast('已刷新 · 30日已平 ' + (d.closed_positions_30d || 0), true);
      })
      .catch(function (e) {
        console.error(e);
        toast(String(e.message || e), false);
      });
  }

  function loadPositions() {
    return fetch(API + '/positions?limit=80')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = j.data || [];
        var openN = rows.filter(function (r) {
          return String(r.status || '').toUpperCase() === 'OPEN';
        }).length;
        $('pos-count').textContent = openN + ' 开仓 / ' + rows.length + ' 条';
        var body = $('pos-body');
        body.innerHTML = '';
        if (!rows.length) {
          body.innerHTML = '<tr><td colspan="8" class="px-3 py-6 text-on-surface-variant">暂无 BRAIN 持仓</td></tr>';
          return;
        }
        rows.forEach(function (r) {
          var pnl = r.unrealized_pnl != null ? Number(r.unrealized_pnl) : null;
          var pnlCls = pnl == null ? '' : (pnl >= 0 ? 'text-primary' : 'text-error');
          var tr = document.createElement('tr');
          tr.innerHTML =
            '<td class="px-3 py-2 mono">' + r.id + '</td>' +
            '<td class="px-3 py-2 mono">' + (r.symbol || '--') + '</td>' +
            '<td class="px-3 py-2">' + sideChip(r.position_side) + '</td>' +
            '<td class="px-3 py-2 mono">' + (r.status || '--') + '</td>' +
            '<td class="px-3 py-2 mono">' + fmtNum(r.entry_price) + '</td>' +
            '<td class="px-3 py-2 mono ' + pnlCls + '">' + (pnl == null ? '--' : fmtNum(pnl, 2)) + '</td>' +
            '<td class="px-3 py-2 mono">' + (r.source || '--') + '</td>' +
            '<td class="px-3 py-2 mono">' + fmtTime(r.open_time || r.created_at) + '</td>';
          body.appendChild(tr);
        });
      })
      .catch(function (e) {
        console.error(e);
        toast('持仓加载失败: ' + (e.message || e), false);
      });
  }

  function refreshAll() {
    return Promise.all([loadOverview(), loadPositions()]);
  }

  function bind() {
    $('btn-refresh').addEventListener('click', function () { refreshAll(); });
    $('btn-run').addEventListener('click', function () {
      var btn = $('btn-run');
      btn.disabled = true;
      toast('正在启动一轮…', true);
      fetch(API + '/run', { method: 'POST' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok || !res.j.success) throw new Error((res.j && (res.j.detail || res.j.message)) || 'run failed');
          toast(res.j.message || '已启动', true);
          setTimeout(refreshAll, 2500);
        })
        .catch(function (e) { toast(String(e.message || e), false); })
        .finally(function () { btn.disabled = false; });
    });
    $('tog-enabled').addEventListener('change', function () {
      var enabled = !!$('tog-enabled').checked;
      fetch(API + '/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled }),
      })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.success) throw new Error(j.detail || 'toggle failed');
          toast(enabled ? '已开启 brain_swing' : '已关闭 brain_swing', true);
        })
        .catch(function (e) {
          $('tog-enabled').checked = !enabled;
          toast(String(e.message || e), false);
        });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { bind(); refreshAll(); });
  } else {
    bind();
    refreshAll();
  }
})();
