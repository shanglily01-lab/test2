/**
 * Custom modal utility — replaces browser alert() and confirm()
 * Requires Tailwind CSS to be loaded on the page.
 */
(function(global) {

  function _ensureContainer() {
    var id = '__modal_container__';
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement('div');
      el.id = id;
      document.body.appendChild(el);
    }
    return el;
  }

  function _inject(html) {
    var c = _ensureContainer();
    c.innerHTML = html;
    return c;
  }

  function _remove() {
    var c = document.getElementById('__modal_container__');
    if (c) c.innerHTML = '';
  }

  var ICON = {
    success: '<span class="material-symbols-outlined text-[32px] text-[#49f4c8]">check_circle</span>',
    error:   '<span class="material-symbols-outlined text-[32px] text-[#ff716c]">error</span>',
    warning: '<span class="material-symbols-outlined text-[32px] text-yellow-400">warning</span>',
    info:    '<span class="material-symbols-outlined text-[32px] text-blue-400">info</span>',
    confirm: '<span class="material-symbols-outlined text-[32px] text-yellow-400">help</span>',
  };

  /**
   * showToast(message, type = 'info', duration = 3000)
   * Non-blocking notification toast in top-right corner.
   */
  function showToast(message, type, duration) {
    type = type || 'info';
    duration = duration || 3000;
    var colors = {
      success: 'border-[#49f4c8]/40 bg-[#49f4c8]/10 text-[#49f4c8]',
      error:   'border-[#ff716c]/40 bg-[#ff716c]/10 text-[#ff716c]',
      warning: 'border-yellow-400/40 bg-yellow-400/10 text-yellow-300',
      info:    'border-blue-400/40 bg-blue-400/10 text-blue-300',
    };
    var iconMap = {
      success: 'check_circle', error: 'error', warning: 'warning', info: 'info'
    };
    var cls = colors[type] || colors.info;
    var ico = iconMap[type] || 'info';

    var toastId = '__toast_' + Date.now();
    var toastArea = document.getElementById('__toast_area__');
    if (!toastArea) {
      toastArea = document.createElement('div');
      toastArea.id = '__toast_area__';
      toastArea.style.cssText = 'position:fixed;top:20px;right:20px;z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
      document.body.appendChild(toastArea);
    }
    var div = document.createElement('div');
    div.id = toastId;
    div.style.cssText = 'pointer-events:auto;transition:all 0.3s ease;opacity:0;transform:translateX(20px);';
    div.className = 'flex items-center gap-3 px-4 py-3 rounded-xl border backdrop-blur-sm ' + cls;
    div.innerHTML = '<span class="material-symbols-outlined text-[18px]">' + ico + '</span>' +
      '<span class="text-sm font-medium" style="max-width:280px;word-break:break-word;">' + message + '</span>';
    toastArea.appendChild(div);
    requestAnimationFrame(function() {
      div.style.opacity = '1';
      div.style.transform = 'translateX(0)';
    });
    setTimeout(function() {
      div.style.opacity = '0';
      div.style.transform = 'translateX(20px)';
      setTimeout(function() { if (div.parentNode) div.parentNode.removeChild(div); }, 300);
    }, duration);
  }

  /**
   * showAlert(message, type = 'info')
   * Replaces alert(). Returns a Promise that resolves on OK.
   */
  function showAlert(message, type) {
    type = type || 'info';
    return new Promise(function(resolve) {
      var icon = ICON[type] || ICON.info;
      var btnCls = type === 'error' ? 'bg-[#ff716c]/20 border-[#ff716c]/40 text-[#ff716c] hover:bg-[#ff716c]/30'
                 : type === 'success' ? 'bg-[#49f4c8]/20 border-[#49f4c8]/40 text-[#49f4c8] hover:bg-[#49f4c8]/30'
                 : 'bg-white/10 border-white/20 text-on-surface hover:bg-white/15';
      _inject(
        '<div style="position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:99990;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);">' +
        '<div style="background:#0f1419;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:28px 32px;min-width:320px;max-width:480px;box-shadow:0 24px 64px rgba(0,0,0,0.6);">' +
        '<div style="display:flex;flex-direction:column;align-items:center;gap:16px;text-align:center;">' +
        icon +
        '<p style="color:#ebeef5;font-size:14px;line-height:1.6;font-family:Manrope,sans-serif;">' + message + '</p>' +
        '<button id="__modal_ok__" class="' + btnCls + '" style="border-width:1px;border-style:solid;padding:8px 28px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;transition:all 0.15s;font-family:Space Grotesk,sans-serif;letter-spacing:0.05em;">确 定</button>' +
        '</div></div></div>'
      );
      document.getElementById('__modal_ok__').onclick = function() { _remove(); resolve(); };
    });
  }

  /**
   * showConfirm(message, options = {})
   * Replaces confirm(). Returns a Promise<boolean>.
   * options: { title, confirmText, cancelText, type }
   */
  function showConfirm(message, options) {
    options = options || {};
    var type = options.type || 'confirm';
    var icon = ICON[type] || ICON.confirm;
    var confirmText = options.confirmText || '确 认';
    var cancelText  = options.cancelText  || '取 消';
    var title       = options.title || '';
    return new Promise(function(resolve) {
      _inject(
        '<div style="position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:99990;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);">' +
        '<div style="background:#0f1419;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:28px 32px;min-width:320px;max-width:480px;box-shadow:0 24px 64px rgba(0,0,0,0.6);">' +
        '<div style="display:flex;flex-direction:column;align-items:center;gap:16px;text-align:center;">' +
        icon +
        (title ? '<p style="color:#ebeef5;font-size:15px;font-weight:700;font-family:Space Grotesk,sans-serif;">' + title + '</p>' : '') +
        '<p style="color:#9aa3af;font-size:13px;line-height:1.6;font-family:Manrope,sans-serif;">' + message + '</p>' +
        '<div style="display:flex;gap:10px;margin-top:4px;">' +
        '<button id="__modal_cancel__" style="padding:8px 24px;border-radius:8px;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.05);color:#9aa3af;font-size:13px;font-weight:700;cursor:pointer;font-family:Space Grotesk,sans-serif;">' + cancelText + '</button>' +
        '<button id="__modal_confirm__" style="padding:8px 24px;border-radius:8px;border:1px solid rgba(255,113,108,0.5);background:rgba(255,113,108,0.15);color:#ff716c;font-size:13px;font-weight:700;cursor:pointer;font-family:Space Grotesk,sans-serif;">' + confirmText + '</button>' +
        '</div></div></div></div>'
      );
      document.getElementById('__modal_confirm__').onclick = function() { _remove(); resolve(true); };
      document.getElementById('__modal_cancel__').onclick  = function() { _remove(); resolve(false); };
    });
  }

  global.showAlert   = showAlert;
  global.showConfirm = showConfirm;
  global.showToast   = global.showToast || showToast;

})(window);
