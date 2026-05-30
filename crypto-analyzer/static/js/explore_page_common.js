/**
 * Gemini / DeepSeek 探索页共用 UI（run 详情、verdicts 表、复制、通知）
 * 依赖: fmt, escapeHtml, modal.js (showToast / showAlert)
 */
(function (global) {
  var _runDetailCache = {};

  function exploreNotify(message, type) {
    type = type || 'info';
    if (type === 'success' || type === 'info') {
      if (typeof global.showToast === 'function') {
        global.showToast(message, type === 'success' ? 'success' : 'info');
        return;
      }
    }
    if (typeof global.showAlert === 'function') {
      global.showAlert(message, type === 'success' ? 'success' : type);
      return;
    }
    global.alert(message);
  }

  function aiLogBadge(r) {
    if (!r.has_prompt && !r.has_raw) return '';
    return '<span class="ai-log-badge" title="已保存 Prompt / 原始响应"><span class="material-symbols-outlined text-[12px]">description</span></span>';
  }

  function buildCodeBlock(text) {
    return '<div class="code-block-wrap"><pre class="code-block">' + escapeHtml(text) + '</pre>' +
      '<div class="code-block-toolbar">' +
      '<button type="button" class="code-copy-btn" onclick="copyCodeBlock(this)">' +
      '<span class="material-symbols-outlined text-[14px]">content_copy</span>复制</button></div></div>';
  }

  function formatRawJson(text) {
    if (!text) return '<p class="text-on-surface-variant text-xs">无数据（此轮可能在开启记录功能之前运行）</p>';
    try {
      return buildCodeBlock(JSON.stringify(JSON.parse(text), null, 2));
    } catch (e) {
      return buildCodeBlock(text);
    }
  }

  function copyCodeBlock(btn) {
    var wrap = btn.closest('.code-block-wrap');
    var pre = wrap ? wrap.querySelector('pre.code-block') : null;
    if (!pre) return;
    navigator.clipboard.writeText(pre.textContent).then(function () {
      var old = btn.innerHTML;
      btn.innerHTML = '<span class="material-symbols-outlined text-[14px]">check</span>已复制';
      setTimeout(function () { btn.innerHTML = old; }, 1500);
    }).catch(function () {
      exploreNotify('复制失败，请手动选择文本', 'error');
    });
  }

  function buildRunDetailShell(runId, prefix, apiPrefix, verdictsHtml) {
    return '<div class="run-detail-wrap" id="run-wrap-' + prefix + runId + '" data-api="' + apiPrefix + '">' +
      '<div class="run-tab-bar">' +
      '<button type="button" class="run-tab active" data-tab="verdicts" onclick="switchRunTab(\'' + prefix + '\',' + runId + ',\'verdicts\')">Verdicts</button>' +
      '<button type="button" class="run-tab" data-tab="prompt" onclick="switchRunTab(\'' + prefix + '\',' + runId + ',\'prompt\')">Prompt</button>' +
      '<button type="button" class="run-tab" data-tab="raw" onclick="switchRunTab(\'' + prefix + '\',' + runId + ',\'raw\')">原始 JSON</button>' +
      '</div>' +
      '<div class="run-tab-panel" id="run-panel-' + prefix + runId + '-verdicts">' + verdictsHtml + '</div>' +
      '<div class="run-tab-panel hidden" id="run-panel-' + prefix + runId + '-prompt"><p class="text-on-surface-variant text-xs">加载中...</p></div>' +
      '<div class="run-tab-panel hidden" id="run-panel-' + prefix + runId + '-raw"><p class="text-on-surface-variant text-xs">加载中...</p></div>' +
      '</div>';
  }

  function switchRunTab(prefix, runId, tab) {
    var wrap = document.getElementById('run-wrap-' + prefix + runId);
    if (!wrap) return;
    wrap.querySelectorAll('.run-tab').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
    });
    ['verdicts', 'prompt', 'raw'].forEach(function (name) {
      var panel = document.getElementById('run-panel-' + prefix + runId + '-' + name);
      if (panel) panel.classList.toggle('hidden', name !== tab);
    });
    if (tab === 'prompt' || tab === 'raw') {
      loadRunDetail(prefix, runId, tab);
    }
  }

  function loadRunDetail(prefix, runId, tab) {
    var cacheKey = prefix + runId + ':' + tab;
    var panel = document.getElementById('run-panel-' + prefix + runId + '-' + tab);
    if (!panel) return;
    if (_runDetailCache[cacheKey]) {
      panel.innerHTML = _runDetailCache[cacheKey];
      return;
    }
    var wrap = document.getElementById('run-wrap-' + prefix + runId);
    var apiPrefix = wrap ? wrap.getAttribute('data-api') : '';
    fetch(apiPrefix + '/runs/' + runId + '/detail')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) throw new Error(d.detail || 'load failed');
        var data = d.data || {};
        var html;
        if (tab === 'prompt') {
          html = buildCodeBlock(data.prompt_text || '');
          if (data.summary_zh) {
            html = '<p class="text-xs text-on-surface-variant mb-2"><span class="text-primary">summary_zh:</span> ' + escapeHtml(data.summary_zh) + '</p>' + html;
          }
        } else {
          html = formatRawJson(data.raw_response || '');
          if (data.summary_zh) {
            html = '<p class="text-xs text-on-surface-variant mb-2"><span class="text-primary">summary_zh:</span> ' + escapeHtml(data.summary_zh) + '</p>' + html;
          }
        }
        _runDetailCache[cacheKey] = html;
        panel.innerHTML = html;
      })
      .catch(function (e) {
        panel.innerHTML = '<p class="text-on-surface-variant text-xs">加载失败: ' + escapeHtml(String(e)) + '</p>';
      });
  }

  function buildVerdictsTable(vs, includePrice, openedOnly) {
    if (openedOnly) {
      vs = (vs || []).filter(function (v) { return v.action_taken === 'opened'; });
    }
    if (!vs.length) {
      return '<p class="text-on-surface-variant text-xs">' + (openedOnly ? '本轮无开仓' : '无 verdicts') + '</p>';
    }
    var html = '<div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="border-b border-outline-variant/20 text-on-surface-variant">' +
      '<th class="px-2 py-2 text-left">Symbol</th><th class="px-2 py-2 text-left">类别</th><th class="px-2 py-2 text-right">置信</th>';
    if (includePrice) html += '<th class="px-2 py-2 text-right">预测价</th>';
    html += '<th class="px-2 py-2 text-left">动作</th><th class="px-2 py-2 text-left">催化剂</th>';
    if (!includePrice) html += '<th class="px-2 py-2 text-left">数据信号</th>';
    if (!openedOnly) html += '<th class="px-2 py-2 text-left">跳过原因</th>';
    html += '</tr></thead><tbody>';
    vs.forEach(function (v) {
      var catRaw = (v.category || '').toLowerCase();
      var catChip = (catRaw === 'bullish' || catRaw === 'red_swan') ? '<span class="chip chip-long">看多</span>'
        : (catRaw === 'bearish' || catRaw === 'black_swan') ? '<span class="chip chip-short">看空</span>'
          : (catRaw === 'entry' || catRaw === 'signal') ? '<span class="chip chip-long">入场</span>'
            : '<span class="chip chip-skip">SKIP</span>';
      var act = v.action_taken || 'skipped_other';
      var actClass = act === 'opened' ? 'chip-action-opened'
        : act === 'skipped_big4' ? 'chip-action-big4'
          : act === 'skipped_dedup' ? 'chip-action-dedup'
            : act === 'skipped_confidence' ? 'chip-action-confidence'
              : 'chip-action-other';
      var actLabel = act === 'opened' ? ('已开仓 #' + (v.position_id || '?'))
        : act === 'skipped_big4' ? 'Big4 闸门'
          : act === 'skipped_dedup' ? '已有同向'
            : act === 'skipped_max_positions' ? '已达上限'
              : act === 'skipped_confidence' ? '低置信度'
                : act === 'skipped_weak_catalyst' ? '催化剂不足'
                  : '其他';
      html += '<tr class="border-b border-outline-variant/10">' +
        '<td class="px-2 py-2 mono">' + escapeHtml(v.symbol) + '</td>' +
        '<td class="px-2 py-2">' + catChip + '</td>' +
        '<td class="px-2 py-2 text-right mono">' + fmt(v.confidence, 2) + '</td>';
      if (includePrice) html += '<td class="px-2 py-2 text-right mono text-on-surface-variant">' + fmt(v.price_at_pred, 6) + '</td>';
      html += '<td class="px-2 py-2"><span class="chip ' + actClass + '">' + actLabel + '</span></td>' +
        '<td class="px-2 py-2 text-on-surface-variant text-[11px] max-w-xs">' + escapeHtml(v.catalyst || '') + '</td>';
      if (!includePrice) html += '<td class="px-2 py-2 text-on-surface-variant text-[11px] max-w-xs">' + escapeHtml(v.data_signal || '') + '</td>';
      if (!openedOnly) html += '<td class="px-2 py-2 text-on-surface-variant text-[11px]">' + escapeHtml(v.skip_reason || '') + '</td></tr>';
      else html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  global.exploreNotify = exploreNotify;
  global.aiLogBadge = aiLogBadge;
  global.buildCodeBlock = buildCodeBlock;
  global.formatRawJson = formatRawJson;
  global.copyCodeBlock = copyCodeBlock;
  global.buildRunDetailShell = buildRunDetailShell;
  global.switchRunTab = switchRunTab;
  global.loadRunDetail = loadRunDetail;
  global.buildVerdictsTable = buildVerdictsTable;
})(window);
