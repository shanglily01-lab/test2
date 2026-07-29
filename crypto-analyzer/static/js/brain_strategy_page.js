/**
 * 超级大脑策略页 — 实时轮询直播 / 机会 / 持仓
 */
(function () {
  var API = '/api/brain-swing';
  var liveTimer = null;

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

  function fmtPct(v) {
    if (v == null || v === '') return '--';
    var n = Number(v);
    if (!isFinite(n)) return '--';
    return (n * 100).toFixed(1) + '%';
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
        if (p.tick_batch_size) {
          $('p-interval').textContent = p.tick_batch_size + '币 / ' + p.tick_interval_seconds + 's';
        }
        $('p-sltp').textContent = 'SL ' + p.sl_pct + '% / TP ' + p.tp_pct + '% · ' + p.hold_hours + 'h / ' + p.leverage + 'x';
        $('p-counts').textContent = '仓 ' + (d.open_positions || 0) + ' · 挂 ' + (d.pending_limits || 0);
        if (b4.ok) {
          $('p-big4').textContent = '可交易';
          $('p-big4').style.color = '#49f4c8';
        } else {
          $('p-big4').textContent = '疲软';
          $('p-big4').style.color = '#ff716c';
        }
        $('p-big4-bias').textContent = 'bias ' + (b4.bias || 'FLAT') + (b4.reason ? ' · ' + b4.reason : '');
      })
      .catch(function (e) {
        console.error(e);
        toast(String(e.message || e), false);
      });
  }

  function renderLiveBatch(batch) {
    var body = $('live-batch-body');
    if (!body) return;
    body.innerHTML = '';
    if (!batch || !batch.length) {
      body.innerHTML = '<tr><td colspan="6" class="px-3 py-4 text-on-surface-variant">本批暂无结果</td></tr>';
      return;
    }
    batch.forEach(function (r) {
      var sigs = r.signals || [];
      var decCls = r.decision === 'OPENED' ? 'text-primary font-semibold' : 'text-on-surface-variant';
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="px-3 py-2 mono">' + (r.symbol || '--') + '</td>' +
        '<td class="px-3 py-2 mono font-semibold">' + (r.playbook || '--') + '</td>' +
        '<td class="px-3 py-2">' + sideChip(r.side) + '</td>' +
        '<td class="px-3 py-2 mono ' + decCls + '">' + (r.decision || '--') + '</td>' +
        '<td class="px-3 py-2 mono text-[10px] max-w-[200px] truncate" title="' + sigs.join(',') + '">' +
          (sigs.slice(0, 4).join(', ') || '--') + '</td>' +
        '<td class="px-3 py-2 text-[10px] text-on-surface-variant max-w-[160px] truncate" title="' +
          (r.skip_reason || r.evidence_summary || '') + '">' +
          (r.skip_reason || '--') + '</td>';
      body.appendChild(tr);
    });
  }

  function loadLive() {
    return fetch(API + '/live')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.success) return;
        var d = j.data || {};
        var analyzing = !!d.analyzing;
        var chip = $('live-analyzing');
        if (chip) {
          chip.textContent = analyzing ? '分析中' : (d.enabled === false ? '已关闭' : '轮询中');
          chip.className = 'chip ' + (analyzing ? 'chip-on' : (d.enabled === false ? 'chip-off' : 'chip-warn'));
        }
        var size = d.pool_size || 0;
        var cur = d.cursor || 0;
        var pct = d.progress_pct != null ? d.progress_pct : (size ? (100 * cur / size) : 0);
        if ($('p-progress')) $('p-progress').textContent = cur + ' / ' + size;
        if ($('p-laps')) $('p-laps').textContent = '圈 ' + (d.laps || 0) + ' · tick ' + (d.tick_count || 0);
        if ($('live-bar')) $('live-bar').style.width = Math.min(100, pct) + '%';
        if ($('p-next')) {
          var nxt = d.next_symbols || [];
          $('p-next').textContent = nxt.length ? ('下批 ' + nxt.join(', ')) : '下批 --';
        }
        if ($('live-tick-at')) $('live-tick-at').textContent = d.last_tick_at ? ('上次 ' + d.last_tick_at) : '--';
        renderLiveBatch(d.last_batch || []);
        var st = d.stats || {};
        if ($('live-stats')) {
          $('live-stats').textContent =
            '累计 识别 ' + (st.opportunities || 0) +
            ' · 开仓 ' + (st.opened || 0) +
            ' · 跳过 ' + (st.skipped || 0) +
            ' · 平仓 ' + (st.closed || 0) +
            (d.last_error ? (' · err ' + d.last_error) : '');
        }
        if (d.big4 && $('p-big4')) {
          if (d.big4.ok) {
            $('p-big4').textContent = '可交易';
            $('p-big4').style.color = '#49f4c8';
          } else if (d.big4.ok === false) {
            $('p-big4').textContent = '疲软';
            $('p-big4').style.color = '#ff716c';
          }
          if ($('p-big4-bias') && d.big4.bias) {
            $('p-big4-bias').textContent = 'bias ' + d.big4.bias;
          }
        }
      })
      .catch(function (e) { console.error(e); });
  }

  function loadOpportunities() {
    var pb = ($('sel-pb-filter') && $('sel-pb-filter').value) || '';
    var dec = ($('sel-dec-filter') && $('sel-dec-filter').value) || '';
    var url = API + '/opportunities?limit=100';
    if (pb) url += '&playbook=' + encodeURIComponent(pb);
    if (dec) url += '&decision=' + encodeURIComponent(dec);
    return fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = j.data || [];
        var body = $('opp-body');
        if (!body) return;
        body.innerHTML = '';
        if (!rows.length) {
          body.innerHTML = '<tr><td colspan="9" class="px-3 py-6 text-on-surface-variant">暂无机会记录 — 等待轮询</td></tr>';
          return;
        }
        rows.forEach(function (r) {
          var sigs = r.signals;
          if (typeof sigs === 'string') {
            try { sigs = JSON.parse(sigs); } catch (e) { sigs = []; }
          }
          if (!Array.isArray(sigs)) sigs = [];
          var tr = document.createElement('tr');
          var decCls = r.decision === 'OPENED' ? 'text-primary' : 'text-on-surface-variant';
          tr.innerHTML =
            '<td class="px-3 py-2 mono">' + r.id + '</td>' +
            '<td class="px-3 py-2 mono">' + (r.symbol || '--') + '</td>' +
            '<td class="px-3 py-2 mono font-semibold">' + (r.playbook || '--') + '</td>' +
            '<td class="px-3 py-2">' + sideChip(r.side) + '</td>' +
            '<td class="px-3 py-2 mono ' + decCls + '">' + (r.decision || '--') + '</td>' +
            '<td class="px-3 py-2 mono">' + (r.order_id != null ? r.order_id : '--') + '</td>' +
            '<td class="px-3 py-2 mono">' + fmtPct(r.win_prob_long) + ' / ' + fmtPct(r.win_prob_short) + '</td>' +
            '<td class="px-3 py-2 mono text-[10px] max-w-[220px] truncate" title="' + sigs.join(',') + '">' +
              (sigs.slice(0, 4).join(', ') || '--') + '</td>' +
            '<td class="px-3 py-2 text-[10px] text-on-surface-variant max-w-[180px] truncate" title="' +
              (r.skip_reason || r.evidence_summary || '') + '">' +
              (r.skip_reason || (r.evidence_summary || '').slice(0, 40) || '--') + '</td>';
          body.appendChild(tr);
        });
      })
      .catch(function (e) {
        console.error(e);
      });
  }

  function loadOrders() {
    return fetch(API + '/orders?limit=50')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = j.data || [];
        var pending = j.pending != null ? j.pending : rows.filter(function (r) {
          return String(r.status || '').toUpperCase() === 'PENDING';
        }).length;
        if ($('ord-count')) $('ord-count').textContent = pending + ' 挂单 / ' + rows.length + ' 条';
        var body = $('ord-body');
        if (!body) return;
        body.innerHTML = '';
        if (!rows.length) {
          body.innerHTML = '<tr><td colspan="8" class="px-3 py-6 text-on-surface-variant">暂无限价单</td></tr>';
          return;
        }
        rows.forEach(function (r) {
          var st = String(r.status || '').toUpperCase();
          var stCls = st === 'PENDING' ? 'text-primary' : 'text-on-surface-variant';
          var sideRaw = String(r.side || '');
          var sideShow = sideRaw.indexOf('SHORT') >= 0 ? 'SHORT' : (sideRaw.indexOf('LONG') >= 0 ? 'LONG' : sideRaw);
          var tr = document.createElement('tr');
          tr.innerHTML =
            '<td class="px-3 py-2 mono">' + r.id + '</td>' +
            '<td class="px-3 py-2 mono">' + (r.symbol || '--') + '</td>' +
            '<td class="px-3 py-2">' + sideChip(sideShow) + '</td>' +
            '<td class="px-3 py-2 mono ' + stCls + '">' + (r.status || '--') + '</td>' +
            '<td class="px-3 py-2 mono">' + fmtNum(r.price) + '</td>' +
            '<td class="px-3 py-2 mono">' + fmtNum(r.quantity, 4) + '</td>' +
            '<td class="px-3 py-2 mono">' + (r.order_source || '--') + '</td>' +
            '<td class="px-3 py-2 mono">' + fmtTime(r.created_at) + '</td>';
          body.appendChild(tr);
        });
      })
      .catch(function (e) { console.error(e); });
  }

  function loadPlaybookStats() {
    return fetch(API + '/playbook-stats?days=30')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = j.data || [];
        var el = $('pb-stats');
        if (!el) return;
        if (!rows.length) {
          el.textContent = '近30日暂无按 playbook 统计';
          return;
        }
        el.textContent = rows.map(function (r) {
          return r.playbook + ':' + r.identified +
            '(开' + (r.opened || 0) + '/跳' + (r.skipped || 0) + ')';
        }).join(' · ');
      })
      .catch(function () {});
  }

  function loadPositions() {
    return fetch(API + '/positions?limit=80')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = j.data || [];
        var openN = rows.filter(function (r) {
          return String(r.status || '').toUpperCase() === 'OPEN';
        }).length;
        if ($('pos-count')) $('pos-count').textContent = openN + ' 开仓 / ' + rows.length + ' 条';
        var body = $('pos-body');
        if (!body) return;
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
      .catch(function (e) { console.error(e); });
  }

  function refreshSlow() {
    return Promise.all([loadOverview(), loadOpportunities(), loadPlaybookStats(), loadOrders(), loadPositions()]);
  }

  function startLivePoll() {
    if (liveTimer) clearInterval(liveTimer);
    loadLive();
    liveTimer = setInterval(function () {
      loadLive();
    }, 5000);
  }

  function bind() {
    $('btn-refresh').addEventListener('click', function () {
      refreshSlow();
      loadLive();
    });
    if ($('sel-pb-filter')) $('sel-pb-filter').addEventListener('change', loadOpportunities);
    if ($('sel-dec-filter')) $('sel-dec-filter').addEventListener('change', loadOpportunities);
    $('btn-run').addEventListener('click', function () {
      var btn = $('btn-run');
      btn.disabled = true;
      toast('触发一批轮询…', true);
      fetch(API + '/run', { method: 'POST' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok || !res.j.success) throw new Error((res.j && (res.j.detail || res.j.message)) || 'run failed');
          toast(res.j.message || '已触发', true);
          setTimeout(function () { loadLive(); loadOpportunities(); loadOrders(); loadOverview(); }, 2000);
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
          toast(enabled ? '已开启轮询' : '已关闭轮询', true);
          loadLive();
        })
        .catch(function (e) {
          $('tog-enabled').checked = !enabled;
          toast(String(e.message || e), false);
        });
    });
    // 机会表慢刷
    setInterval(function () { loadOpportunities(); loadOrders(); }, 20000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      bind();
      refreshSlow();
      startLivePoll();
    });
  } else {
    bind();
    refreshSlow();
    startLivePoll();
  }
})();
