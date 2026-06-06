(function () {
  "use strict";

  var STORAGE_KEY = "system_language";

  var keyed = {
    en: {
      "nav.features": "Features",
      "nav.docs": "Docs",
      "nav.ops": "Ops",
      "nav.login": "Login",
      "nav.enter": "Enter App",
      "hero.badge": "Quant Terminal / 2026",
      "hero.subtitle": "A Binance USD-M quant terminal with three AI teachers, Big4 market analysis, open and hold advisors, and separated paper/live execution.",
      "hero.primary": "Open Dashboard",
      "hero.secondary": "View AI Explore",
      "terminal.eyebrow": "Live Control Surface",
      "terminal.title": "AI Trading Console",
      "metric.sync": "Live Sync Sources",
      "metric.interval": "AI Cycle",
      "metric.polling": "due polling guard",
      "metric.risk": "Risk Gates",
      "metric.whitelist": "or whitelist",
      "metric.big4": "Big4 Analysis",
      "features.eyebrow": "Current System",
      "features.title": "Only active trading paths remain",
      "features.subtitle": "Legacy tactical, reversal, and S-series strategies are offline. The homepage now reflects only maintained modules.",
      "card.explore.title": "Three-Teacher Explore and Predict",
      "card.explore.body": "Gemini, DeepSeek, and GPT keep the main explore and predict flows on a 4-hour cadence with catalyst and multi-timeframe filters.",
      "card.big4.title": "Big4 Market Analysis",
      "card.big4.body": "Gemini and DeepSeek analyze BTC, ETH, BNB, and SOL every 4 hours for macro context, not direct trade execution.",
      "card.gates.title": "Live Trading Gates",
      "card.gates.body": "Only Gemini and DeepSeek explore/predict sources can sync live, gated by master switch, TOP50, whitelist, and L3 blacklist checks.",
      "card.advisor.title": "Open and Hold Advisors",
      "card.advisor.body": "Orders route to Gemini or DeepSeek advisors by source, reviewing catalysts before entry and risk while positions are open.",
      "card.sentiment.title": "Gemini Market Sentiment",
      "card.sentiment.body": "Runs every 8 hours, records market and event impact in gemini_sentiment_runs, and does not trigger trades directly.",
      "card.shadow.title": "AI Shadow",
      "card.shadow.body": "Compares model outputs and rule behavior for review and tuning without acting as a direct entry path.",
      "docs.eyebrow": "Documentation",
      "docs.title": "System documentation is consolidated",
      "docs.body": "Old docs were cleaned up. The current system is described in one source of truth to avoid legacy strategy drift.",
      "docs.manual": "Open Manual",
      "docs.updated": "Maintained against current code",
      "docs.item1": "Active AI modules, schedules, and table boundaries.",
      "docs.item2": "Live source whitelist, TOP50, whitelist, and L3 blacklist gates.",
      "docs.item3": "Offline strategies and old tables that can be cleaned.",
      "docs.item4": "Open advisors, hold advisors, and AI Shadow.",
      "ops.eyebrow": "Operations",
      "ops.title": "Common Entrypoints",
      "ops.subtitle": "Jump directly into the pages that are still part of the active system.",
      "ops.dashboard": "Market and system status",
      "ops.futures": "Futures Trading",
      "ops.futuresBody": "Paper positions and management",
      "ops.live": "Binance live sync",
      "ops.settings": "System Settings",
      "ops.settingsBody": "Switches, direction, and risk",
      "footer.note": "AI-assisted quant system. Live actions always remain gated by user switches and risk controls."
    }
  };

  var exactZhToEn = {
    "首页": "Home",
    "功能": "Features",
    "文档": "Docs",
    "关于": "About",
    "运维": "Ops",
    "登录": "Login",
    "注册": "Register",
    "退出": "Logout",
    "进入系统": "Enter App",
    "系统设置": "System Settings",
    "系统配置中心": "System Settings",
    "合约交易": "Futures Trading",
    "实盘交易": "Live Trading",
    "现货交易": "Spot Trading",
    "模拟仓": "Paper",
    "实盘": "Live",
    "持仓": "Positions",
    "开仓": "Open",
    "平仓": "Close",
    "刷新": "Refresh",
    "保存": "Save",
    "取消": "Cancel",
    "确认": "Confirm",
    "启用": "Enabled",
    "关闭": "Disabled",
    "开启": "On",
    "关闭全部": "Disable All",
    "总开关": "Master Switch",
    "方向控制": "Direction Control",
    "风险控制": "Risk Control",
    "开仓顾问": "Open Advisor",
    "持仓顾问": "Hold Advisor",
    "顾问审核记录": "Advisor Reviews",
    "市场状态": "Market Regime",
    "技术信号": "Technical Signals",
    "数据管理": "Data Management",
    "操作手册": "Manual",
    "AI 探索": "AI Explore",
    "Gemini 探索": "Gemini Explore",
    "DeepSeek 探索": "DeepSeek Explore",
    "GPT 探索": "GPT Explore",
    "Gemini 预测": "Gemini Predict",
    "DeepSeek 预测": "DeepSeek Predict",
    "GPT 预测": "GPT Predict",
    "预测": "Predict",
    "探索": "Explore",
    "综合行情": "Market Analysis",
    "Big4 综合行情": "Big4 Market Analysis",
    "市场情绪": "Market Sentiment",
    "白名单": "Whitelist",
    "黑名单": "Blacklist",
    "收益": "PnL",
    "胜率": "Win Rate",
    "状态": "Status",
    "时间": "Time",
    "方向": "Side",
    "价格": "Price",
    "数量": "Quantity",
    "策略": "Strategy",
    "来源": "Source",
    "详情": "Details",
    "最近运行": "Recent Runs",
    "运行记录": "Runs",
    "候选池": "Candidate Pool",
    "已开启": "Enabled",
    "已关闭": "Disabled"
  };

  var enToZh = {};
  Object.keys(exactZhToEn).forEach(function (zh) {
    enToZh[exactZhToEn[zh]] = zh;
  });

  var titleZhToEn = {
    "Neural Obsidian - 系统配置中心": "Neural Obsidian - System Settings",
    "Neural Obsidian | 量化交易平台": "Neural Obsidian | Quant Trading Platform",
    "Neural Obsidian - 合约交易": "Neural Obsidian - Futures Trading",
    "NEURAL OBSIDIAN": "NEURAL OBSIDIAN"
  };

  function getLanguage() {
    var value = localStorage.getItem(STORAGE_KEY) || localStorage.getItem("home_language") || "zh";
    return value === "en" ? "en" : "zh";
  }

  function setLanguage(lang) {
    localStorage.setItem(STORAGE_KEY, lang === "en" ? "en" : "zh");
    localStorage.removeItem("home_language");
  }

  function ensureFloatingToggle() {
    if (document.getElementById("languageToggle")) return document.getElementById("languageToggle");

    var button = document.createElement("button");
    button.id = "systemLanguageToggle";
    button.type = "button";
    button.setAttribute("aria-label", "Switch language");
    button.style.cssText = [
      "position:fixed",
      "right:18px",
      "bottom:18px",
      "z-index:9999",
      "display:inline-flex",
      "align-items:center",
      "gap:8px",
      "height:40px",
      "padding:0 12px",
      "border-radius:10px",
      "border:1px solid rgba(73,244,200,.35)",
      "background:rgba(10,15,20,.88)",
      "color:#ebeef5",
      "font:700 13px Manrope,system-ui,sans-serif",
      "box-shadow:0 10px 28px rgba(0,0,0,.35)",
      "backdrop-filter:blur(14px)",
      "cursor:pointer"
    ].join(";");
    button.innerHTML = '<span style="font-size:15px">文/A</span><span id="systemLanguageLabel"></span>';
    document.body.appendChild(button);
    return button;
  }

  function setToggleLabel(lang) {
    var label = document.getElementById("languageLabel") || document.getElementById("systemLanguageLabel");
    if (label) label.textContent = lang === "en" ? "EN" : "中文";
  }

  function translateKeyed(lang) {
    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      if (!node.dataset.i18nZh) node.dataset.i18nZh = node.textContent;
      var key = node.getAttribute("data-i18n");
      if (lang === "en" && keyed.en[key]) {
        node.textContent = keyed.en[key];
      } else if (lang === "zh") {
        node.textContent = node.dataset.i18nZh;
      }
    });
  }

  function translateExactElement(node, lang) {
    if (!node || node.nodeType !== 1) return;
    if (node.children.length > 0) return;
    if (["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION"].indexOf(node.tagName) >= 0) return;

    var text = (node.textContent || "").trim();
    if (!text) return;
    if (!node.dataset.i18nOriginal && exactZhToEn[text]) node.dataset.i18nOriginal = text;
    if (!node.dataset.i18nOriginal && enToZh[text]) node.dataset.i18nOriginal = enToZh[text];

    var original = node.dataset.i18nOriginal;
    if (!original) return;
    if (lang === "en" && exactZhToEn[original]) {
      node.textContent = exactZhToEn[original];
    } else if (lang === "zh") {
      node.textContent = original;
    }
  }

  function translateExact(lang) {
    document.querySelectorAll("a,button,span,p,h1,h2,h3,h4,label,th,td,div,li").forEach(function (node) {
      translateExactElement(node, lang);
    });
  }

  function applyLanguage(lang) {
    lang = lang === "en" ? "en" : "zh";
    setLanguage(lang);
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    if (!document.documentElement.dataset.originalTitle) {
      document.documentElement.dataset.originalTitle = document.title;
    }
    var originalTitle = document.documentElement.dataset.originalTitle;
    document.title = lang === "en" ? (titleZhToEn[originalTitle] || originalTitle) : originalTitle;
    translateKeyed(lang);
    translateExact(lang);
    setToggleLabel(lang);
  }

  function boot() {
    var toggle = ensureFloatingToggle();
    var lang = getLanguage();
    applyLanguage(lang);
    toggle.addEventListener("click", function () {
      applyLanguage(getLanguage() === "en" ? "zh" : "en");
    });

    var observer = new MutationObserver(function (mutations) {
      var langNow = getLanguage();
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) {
            translateExactElement(node, langNow);
            if (node.querySelectorAll) {
              node.querySelectorAll("[data-i18n],a,button,span,p,h1,h2,h3,h4,label,th,td,div,li").forEach(function (child) {
                if (child.hasAttribute && child.hasAttribute("data-i18n")) translateKeyed(langNow);
                translateExactElement(child, langNow);
              });
            }
          }
        });
      });
      setToggleLabel(langNow);
    });
    observer.observe(document.body, { childList: true, subtree: true });

    window.SystemI18n = {
      getLanguage: getLanguage,
      setLanguage: applyLanguage
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
