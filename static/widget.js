/**
 * AI Chat Widget
 *
 * Встраиваемый чат-виджет для Tilda и любых сайтов.
 * Без зависимостей. Конфигурируется через window.AIChatConfig.
 *
 * Пример подключения (два HTML-блока на Tilda):
 *
 * Блок 1 — конфигурация (ПЕРЕД виджетом):
 *   <script>
 *     window.AIChatConfig = {
 *       tenantId: "coworking",
 *       apiUrl: "https://your-domain.ru",
 *       greeting: "Здравствуйте! Чем могу помочь?",
 *       botName: "Алина"
 *     };
 *   </script>
 *
 * Блок 2 — виджет:
 *   <script src="https://your-domain.ru/static/widget.js"></script>
 */
(function () {
  'use strict';

  // ── Config ────────────────────────────────────────────────────────────────
  var cfg = window.AIChatConfig || {};
  var TENANT_ID     = cfg.tenantId  || 'coworking';
  var API_URL       = (cfg.apiUrl   || '').replace(/\/$/, '');
  var GREETING      = cfg.greeting  || 'Здравствуйте! Чем могу помочь?';
  var BOT_NAME      = cfg.botName   || 'Ассистент';

  var STORAGE_KEY   = 'ai_chat_session_' + TENANT_ID;
  var POLL_INTERVAL = 3000;

  // ── State ─────────────────────────────────────────────────────────────────
  var sessionId        = localStorage.getItem(STORAGE_KEY) || null;
  var isOpen           = false;
  var pollTimer        = null;
  var lastMsgCount     = 0;
  var greetingShown    = false;

  // ── Styles ────────────────────────────────────────────────────────────────
  function injectStyles() {
    var css = [
      '.ai-chat-bubble{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:#2563eb;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 12px rgba(0,0,0,.25);z-index:999998;transition:transform .15s;}',
      '.ai-chat-bubble:hover{transform:scale(1.08);}',
      '.ai-chat-bubble svg{width:28px;height:28px;fill:#fff;}',
      '.ai-chat-window{position:fixed;bottom:92px;right:24px;width:360px;max-width:calc(100vw - 48px);height:480px;max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.18);display:flex;flex-direction:column;z-index:999997;overflow:hidden;opacity:0;pointer-events:none;transform:translateY(12px);transition:opacity .2s,transform .2s;}',
      '.ai-chat-window.open{opacity:1;pointer-events:auto;transform:translateY(0);}',
      '.ai-chat-header{background:#2563eb;color:#fff;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;font-weight:600;font-size:15px;font-family:sans-serif;}',
      '.ai-chat-close{background:none;border:none;color:#fff;cursor:pointer;font-size:20px;line-height:1;padding:0 4px;opacity:.85;}',
      '.ai-chat-close:hover{opacity:1;}',
      '.ai-chat-messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;font-family:sans-serif;font-size:14px;}',
      '.ai-msg{max-width:80%;padding:8px 12px;border-radius:12px;line-height:1.45;word-wrap:break-word;}',
      '.ai-msg-user{align-self:flex-end;background:#2563eb;color:#fff;border-bottom-right-radius:4px;}',
      '.ai-msg-bot{align-self:flex-start;background:#f3f4f6;color:#111827;border-bottom-left-radius:4px;}',
      '.ai-msg-operator{align-self:flex-start;background:#fef3c7;color:#92400e;border-bottom-left-radius:4px;}',
      '.ai-msg-label{display:block;font-size:11px;font-weight:600;margin-bottom:3px;opacity:.7;}',
      '.ai-chat-footer{padding:10px;border-top:1px solid #e5e7eb;display:flex;gap:8px;background:#fff;}',
      '.ai-chat-input{flex:1;border:1px solid #d1d5db;border-radius:8px;padding:8px 10px;font-size:14px;font-family:sans-serif;resize:none;line-height:1.4;outline:none;}',
      '.ai-chat-input:focus{border-color:#2563eb;}',
      '.ai-chat-send{background:#2563eb;color:#fff;border:none;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:14px;font-family:sans-serif;white-space:nowrap;}',
      '.ai-chat-send:hover{background:#1d4ed8;}',
    ].join('');
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ── DOM ───────────────────────────────────────────────────────────────────
  function createBubble() {
    var el = document.createElement('div');
    el.className = 'ai-chat-bubble';
    el.setAttribute('title', BOT_NAME);
    el.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>';
    return el;
  }

  function createWindow() {
    var el = document.createElement('div');
    el.className = 'ai-chat-window';
    el.innerHTML =
      '<div class="ai-chat-header">' +
        '<span>' + BOT_NAME + '</span>' +
        '<button class="ai-chat-close" title="Закрыть">&#x2715;</button>' +
      '</div>' +
      '<div class="ai-chat-messages"></div>' +
      '<div class="ai-chat-footer">' +
        '<textarea class="ai-chat-input" rows="1" placeholder="Введите сообщение..."></textarea>' +
        '<button class="ai-chat-send">&#10148;</button>' +
      '</div>';
    return el;
  }

  // ── Message rendering ─────────────────────────────────────────────────────
  function appendMessage(text, role) {
    var container = document.querySelector('.ai-chat-messages');
    if (!container) return;
    var div = document.createElement('div');
    div.className = 'ai-msg ai-msg-' + (role === 'user' ? 'user' : role === 'operator' ? 'operator' : 'bot');
    var label = '';
    if (role === 'operator') label = '<span class="ai-msg-label">Оператор</span>';
    else if (role === 'bot')  label = '<span class="ai-msg-label">' + BOT_NAME + '</span>';
    div.innerHTML = label + escapeHtml(text).replace(/\n/g, '<br>');
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  function escapeHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Toggle ────────────────────────────────────────────────────────────────
  var _win;

  function toggleChat() {
    isOpen = !isOpen;
    if (isOpen) {
      _win.classList.add('open');
      if (!greetingShown && !sessionId) {
        appendMessage(GREETING, 'bot');
        greetingShown = true;
      }
      startPolling();
      // Auto-resize input
      var input = _win.querySelector('.ai-chat-input');
      if (input) input.focus();
    } else {
      _win.classList.remove('open');
      stopPolling();
    }
  }

  // ── Send ──────────────────────────────────────────────────────────────────
  function sendMessage() {
    var input = _win.querySelector('.ai-chat-input');
    var text = (input.value || '').trim();
    if (!text) return;
    input.value = '';
    input.style.height = '';
    appendMessage(text, 'user');

    var payload = { message: text };
    if (sessionId) payload.session_id = sessionId;

    fetch(API_URL + '/chat/' + TENANT_ID, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.session_id) {
        sessionId = data.session_id;
        localStorage.setItem(STORAGE_KEY, sessionId);
      }
      if (data.reply) {
        appendMessage(data.reply, data.operator_mode ? 'operator' : 'bot');
      }
      lastMsgCount = 0; // will be re-synced by next poll
    })
    .catch(function () {
      appendMessage('Ошибка соединения. Попробуйте ещё раз.', 'bot');
    });
  }

  // ── Polling (operator messages) ───────────────────────────────────────────
  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(pollHistory, POLL_INTERVAL);
  }

  function stopPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  function pollHistory() {
    if (!sessionId || !isOpen) return;
    fetch(API_URL + '/chat/history?session_id=' + sessionId)
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var msgs = data.messages || [];
      if (lastMsgCount === 0) {
        // First poll after open: sync count without re-rendering
        lastMsgCount = msgs.length;
        return;
      }
      if (msgs.length > lastMsgCount) {
        var newMsgs = msgs.slice(lastMsgCount);
        newMsgs.forEach(function (m) {
          if (m.role !== 'user') {
            appendMessage(m.text, m.role);
          }
        });
        lastMsgCount = msgs.length;
      }
    })
    .catch(function () { /* silent */ });
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    injectStyles();
    var bubble = createBubble();
    _win = createWindow();
    document.body.appendChild(bubble);
    document.body.appendChild(_win);

    bubble.addEventListener('click', toggleChat);
    _win.querySelector('.ai-chat-close').addEventListener('click', function (e) {
      e.stopPropagation();
      if (isOpen) toggleChat();
    });
    _win.querySelector('.ai-chat-send').addEventListener('click', sendMessage);
    _win.querySelector('.ai-chat-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    // Auto-grow textarea
    _win.querySelector('.ai-chat-input').addEventListener('input', function () {
      this.style.height = '';
      this.style.height = Math.min(this.scrollHeight, 96) + 'px';
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
