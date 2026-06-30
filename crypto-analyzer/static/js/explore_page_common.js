/**
 * Gemini / DeepSeek 探索页共用 UI（run 详情、verdicts 表、复制、通知、轮询）
 * 依赖: fmt, escapeHtml, modal.js (showToast / showAlert)
 */
(function (global) {
  var _runDetailCache = {};
  var _HTML_CACHE_MAX = 10;
  var _CODE_RENDER_CHUNK = 12000;
  var _CODE_RENDER_MAX = 120000;

  function exploreFetchJson(url, timeoutMs) {
    timeoutMs = timeoutMs || 12000;
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeoutMs) : null;
    var opts = ctrl ? { signal: ctrl.signal } : {};
    return fetch(url, opts)
      .then(function (r) { return r.json(); })
      .finally(function () { if (timer) clearTimeout(timer); });
  }

  function exploreTrimCache(cache, maxKeys) {
    if (!cache || typeof cache !== 'object') return;
    maxKeys = maxKeys || _HTML_CACHE_MAX;
    var keys = Object.keys(cache);
    while (keys.length > maxKeys) {
      delete cache[keys[0]];
      keys = Object.keys(cache);
    }
  }

  function createExplorePollRegistry() {
    var timers = [];
    var paused = document.hidden;

    function tabVisible(tabId) {
      var el = document.getElementById(tabId);
      return !!(el && !el.classList.contains('hidden'));
    }

    function register(fn, ms, guard) {
      var timer = setInterval(function () {
        if (paused) return;
        if (guard && !guard()) return;
        fn();
      }, ms);
      timers.push(timer);
      return timer;
    }

    function clearAll() {
      timers.forEach(function (t) { clearInterval(t); });
      timers = [];
    }

    document.addEventListener('visibilitychange', function () {
      paused = document.hidden;
    });

    return { register: register, clearAll: clearAll, tabVisible: tabVisible };
  }

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
    var raw = text == null ? '' : String(text);
    var id = 'code-' + Math.random().toString(36).slice(2, 10);
    return '<div class="code-block-wrap" data-full-text-id="' + id + '">' +
      '<pre class="code-block" id="' + id + '-pre"></pre>' +
      '<div class="code-block-toolbar">' +
      '<button type="button" class="code-copy-btn" data-copy-id="' + id + '" onclick="copyCodeBlock(this)">' +
      '<span class="material-symbols-outlined text-[14px]">content_copy</span>复制</button></div></div>';
  }

  function renderCodeBlockAsync(wrap, text) {
    if (!wrap) return;
    var pre = wrap.querySelector('pre.code-block');
    if (!pre) return;
    var raw = text == null ? '' : String(text);
    wrap._fullText = raw;
    if (raw.length <= _CODE_RENDER_CHUNK) {
      pre.textContent = raw;
      return;
    }
    pre.textContent = '渲染中…';
    var pos = 0;
    var displayMax = Math.min(raw.length, _CODE_RENDER_MAX);
    function step() {
      var next = Math.min(pos + _CODE_RENDER_CHUNK, displayMax);
      if (pos === 0) pre.textContent = raw.slice(0, next);
      else pre.textContent += raw.slice(pos, next);
      pos = next;
      if (pos < displayMax) {
        setTimeout(step, 0);
      } else if (raw.length > displayMax) {
        pre.textContent += '\n\n… [已截断显示，共 ' + raw.length + ' 字符，请点「复制」获取全文] …';
      }
    }
    setTimeout(step, 0);
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
    if (!wrap || !pre) return;
    var full = wrap._fullText != null ? wrap._fullText : pre.textContent;
    navigator.clipboard.writeText(full).then(function () {
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

  function mountCodePanel(panel, html, rawText) {
    panel.innerHTML = html;
    var wrap = panel.querySelector('.code-block-wrap');
    if (wrap && rawText != null) renderCodeBlockAsync(wrap, rawText);
  }

  function loadRunDetail(prefix, runId, tab) {
    var cacheKey = prefix + runId + ':' + tab;
    var panel = document.getElementById('run-panel-' + prefix + runId + '-' + tab);
    if (!panel) return;
    if (_runDetailCache[cacheKey]) {
      panel.innerHTML = _runDetailCache[cacheKey].html;
      var cachedWrap = panel.querySelector('.code-block-wrap');
      if (cachedWrap && _runDetailCache[cacheKey].raw != null) {
        renderCodeBlockAsync(cachedWrap, _runDetailCache[cacheKey].raw);
      }
      return;
    }
    var wrap = document.getElementById('run-wrap-' + prefix + runId);
    var apiPrefix = wrap ? wrap.getAttribute('data-api') : '';
    var field = tab === 'prompt' ? 'prompt' : 'raw';
    fetch(apiPrefix + '/runs/' + runId + '/detail?field=' + field)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) throw new Error(d.detail || 'load failed');
        var data = d.data || {};
        var rawText = tab === 'prompt' ? (data.prompt_text || '') : (data.raw_response || '');
        var bodyHtml = tab === 'prompt' ? buildCodeBlock(rawText) : formatRawJson(rawText);
        if (data.summary_zh) {
          bodyHtml = '<p class="text-xs text-on-surface-variant mb-2"><span class="text-primary">summary_zh:</span> ' +
            escapeHtml(data.summary_zh) + '</p>' + bodyHtml;
        }
        _runDetailCache[cacheKey] = { html: bodyHtml, raw: rawText };
        exploreTrimCache(_runDetailCache);
        mountCodePanel(panel, bodyHtml, rawText);
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
  global.exploreFetchJson = exploreFetchJson;
  global.aiLogBadge = aiLogBadge;
  global.buildCodeBlock = buildCodeBlock;
  global.formatRawJson = formatRawJson;
  global.copyCodeBlock = copyCodeBlock;
  global.buildRunDetailShell = buildRunDetailShell;
  global.switchRunTab = switchRunTab;
  global.loadRunDetail = loadRunDetail;
  global.buildVerdictsTable = buildVerdictsTable;
  global.exploreTrimCache = exploreTrimCache;
  global.explorePoll = createExplorePollRegistry();
})(window);
