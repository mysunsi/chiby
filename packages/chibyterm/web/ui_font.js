/**
 * Assistant 统一界面字号（小 / 中 / 大）
 * - 写入 html[data-font-scale]，由 ui_font.css 提供 --fs-* token
 * - 本机 localStorage 记忆，跨页面同步
 * - 可选缩放 xterm；掌上页用 remRoot
 */
(function (global) {
  'use strict';

  var STORAGE_KEY = 'assistant-ui-font-scale';
  var VALID = { sm: 1, md: 1, lg: 1 };
  /* 终端 xterm 与 AI 面板正文：小 11 / 中 12 / 大 14 */
  var XTERM = { sm: 11, md: 12, lg: 14 };
  var LABELS = { sm: '小 11px', md: '中 12px', lg: '大 14px' };

  var _opts = { remRoot: false, skipXterm: false };
  var _terms = [];
  var _inited = false;

  function normalize(v) {
    var s = String(v || '').toLowerCase();
    return VALID[s] ? s : 'md';
  }

  function readStored() {
    try {
      return normalize(localStorage.getItem(STORAGE_KEY));
    } catch (_) {
      return 'md';
    }
  }

  function current() {
    var attr = document.documentElement.getAttribute('data-font-scale');
    if (attr && VALID[String(attr).toLowerCase()]) return normalize(attr);
    return readStored();
  }

  function xtermSize(scale) {
    return XTERM[normalize(scale)] || 12;
  }

  function labelOf(scale) {
    return LABELS[normalize(scale)] || LABELS.md;
  }

  function syncButtons(scale) {
    var nodes = document.querySelectorAll('[data-font-choice]');
    for (var i = 0; i < nodes.length; i++) {
      var btn = nodes[i];
      var on = normalize(btn.getAttribute('data-font-choice')) === scale;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    var labs = document.querySelectorAll('[data-font-scale-label]');
    for (var j = 0; j < labs.length; j++) {
      labs[j].textContent = labelOf(scale);
    }
  }

  function applyXterm(scale) {
    if (_opts.skipXterm) return;
    var fs = xtermSize(scale);
    var i;
    for (i = 0; i < _terms.length; i++) {
      try {
        var t = _terms[i];
        if (t.term && t.term.options) t.term.options.fontSize = fs;
        if (t.fit && typeof t.fit.fit === 'function') t.fit.fit();
      } catch (_) {}
    }
    /* index.html 会话表：未显式 registerTerm 时也能跟档位 */
    try {
      if (typeof sessions === 'object' && sessions) {
        Object.keys(sessions).forEach(function (id) {
          var s = sessions[id];
          if (!s || !s.term) return;
          try {
            s.term.options.fontSize = fs;
            if (s.fit && typeof s.fit.fit === 'function') s.fit.fit();
          } catch (_) {}
        });
      }
    } catch (_) {}
  }

  /** 直接写 AI 面板根字号 + CSS 变量（与 xterm 同路径） */
  function applyAiPanel(scale) {
    var fs = xtermSize(scale);
    var px = fs + 'px';
    try {
      document.documentElement.style.setProperty('--fs-chat', px);
      document.documentElement.style.setProperty('--fs-mono', px);
      document.documentElement.style.setProperty('--fs-ui', (fs === 11 ? 12 : fs === 14 ? 15 : 13) + 'px');
    } catch (_) {}
    var roots = document.querySelectorAll(
      '#aiChatPanel, .ai-chat-panel, #aiChatLog, .ai-chat-composer, #nlInput',
    );
    for (var i = 0; i < roots.length; i++) {
      try {
        roots[i].style.setProperty('font-size', px, 'important');
      } catch (_) {}
    }
  }

  function apply(scale, persist) {
    scale = normalize(scale);
    var root = document.documentElement;
    root.setAttribute('data-font-scale', scale);
    if (_opts.remRoot) root.classList.add('ui-rem-root');
    if (persist !== false) {
      try {
        localStorage.setItem(STORAGE_KEY, scale);
      } catch (_) {}
    }
    syncButtons(scale);
    applyXterm(scale);
    applyAiPanel(scale);
    try {
      global.dispatchEvent(
        new CustomEvent('assistant-font-scale', {
          detail: { scale: scale, fontSize: xtermSize(scale) },
        }),
      );
    } catch (_) {}
    return scale;
  }

  function registerTerm(term, fitAddon) {
    if (!term) return;
    _terms.push({ term: term, fit: fitAddon || null });
    if (!_opts.skipXterm) {
      try {
        term.options.fontSize = xtermSize(current());
        if (fitAddon && typeof fitAddon.fit === 'function') fitAddon.fit();
      } catch (_) {}
    }
  }

  function onDocClick(ev) {
    var el = ev.target;
    if (!el || !el.closest) return;
    var btn = el.closest('[data-font-choice]');
    if (!btn) return;
    var choice = btn.getAttribute('data-font-choice');
    if (!choice || !VALID[normalize(choice)]) return;
    ev.preventDefault();
    apply(choice, true);
  }

  function init(opts) {
    _opts = {
      remRoot: !!(opts && opts.remRoot),
      skipXterm: !!(opts && opts.skipXterm),
    };
    /* 无 opts 时：主终端页默认要缩放 xterm */
    if (!opts) {
      _opts.remRoot = false;
      _opts.skipXterm = false;
    }
    apply(readStored(), true);
    if (!_inited) {
      /* capture：菜单内 stopPropagation 仍能收到点击 */
      document.addEventListener('click', onDocClick, true);
      _inited = true;
    }
    return current();
  }

  global.AssistantUiFont = {
    init: init,
    current: current,
    xtermSize: xtermSize,
    labelOf: labelOf,
    registerTerm: registerTerm,
    apply: function (scale) {
      return apply(scale, true);
    },
  };
})(typeof window !== 'undefined' ? window : this);
