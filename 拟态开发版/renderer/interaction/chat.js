// ═══════════════════════════════════════════════════════════
//  Mimic v2 — Chat Interaction
//
//  Press Enter → text input dialog → mock chat response.
//  If backend /api/chat is available, uses it; else mock replies.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  // ── Mock responses ────────────────────────────────────────

  const MOCK_REPLIES = [
    { trigger: ['你好', '嗨', 'hi', 'hello'], replies: ['你好呀！(*´▽`*)', '嗨~ 今天天气不错呢', '你好你好~'] },
    { trigger: ['再见', '拜拜', 'bye'], replies: ['拜拜！(´▽`ʃ♡ƪ)', '回头见~', '要走了吗… (´•̥̥̥ω•̥̥̥`)'] },
    { trigger: ['谢谢', 'thank'], replies: ['不客气！(◕‿◕)', '嘿嘿，小事一桩~'] },
    { trigger: ['名字', '你是谁', '叫什么'], replies: ['我叫拟态！你的桌面小精灵 ✨', '我是拟态~ 像素小人一枚 (￣▽￣)'] },
    { trigger: ['天气', '下雨', '晴天'], replies: ['嗯…我看不到窗外呢… (・_・;)', '不管什么天气，我都会陪着你~'] },
    { trigger: ['可爱', '萌', 'cute'], replies: ['嘿嘿…谢谢！(〃▽〃)', '你也超可爱的！'] },
    { trigger: ['唱歌', '唱'], replies: ['♪ 啦~啦~啦~ ♪', '♪ 我是一只小桌宠~ 每天在桌面等你~ ♪'] },
    { trigger: ['bongo', 'Bongo', 'BONGO'], replies: ['(≧∇≦)ﾉ ♪ 咚咚咚~ ♪', 'Bongo Cat 模式启动！🥁', '♪ 哒哒哒哒~ ♪'] },
    { trigger: [], replies: ['嗯嗯！(・ω・)', '有意思…', '唔…让我想想… (。-`ω´-)', '是呀是呀~', '真的吗？⊙▽⊙'] },
  ];

  function getMockReply(text) {
    const lower = text.toLowerCase();
    for (const entry of MOCK_REPLIES) {
      if (entry.trigger.length === 0) continue; // skip fallback
      for (const t of entry.trigger) {
        if (lower.includes(t)) {
          return entry.replies[Math.floor(Math.random() * entry.replies.length)];
        }
      }
    }
    // Fallback
    const fallback = MOCK_REPLIES.find(e => e.trigger.length === 0);
    return fallback.replies[Math.floor(Math.random() * fallback.replies.length)];
  }

  // ── Chat flow ─────────────────────────────────────────────

  function showChatInput() {
    M.lastActivity = performance.now();

    // Create a simple input dialog
    const overlay = document.createElement('div');
    overlay.style.cssText = `
      position: fixed;
      top: 0; left: 0;
      width: 100%; height: 100%;
      background: rgba(0,0,0,0.3);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 9999;
    `;

    const dialog = document.createElement('div');
    dialog.style.cssText = `
      background: white;
      border-radius: 16px;
      padding: 20px 24px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.2);
      text-align: center;
      font-family: "Microsoft YaHei", sans-serif;
      min-width: 280px;
    `;

    dialog.innerHTML = `
      <div style="font-size: 36px; margin-bottom: 8px;">💬</div>
      <div style="font-size: 14px; color: #666; margin-bottom: 12px;">和拟态说句话吧~</div>
      <input id="mimic-chat-input" type="text"
        style="width: 100%; padding: 8px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; outline: none;"
        placeholder="输入文字…" autofocus />
      <div style="margin-top: 10px; display: flex; gap: 8px; justify-content: center;">
        <button id="mimic-chat-send" style="padding: 6px 20px; background: #FFB347; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 13px;">发送</button>
        <button id="mimic-chat-cancel" style="padding: 6px 16px; background: #eee; color: #666; border: none; border-radius: 8px; cursor: pointer; font-size: 13px;">取消</button>
      </div>
    `;

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    const input = dialog.querySelector('#mimic-chat-input');
    const sendBtn = dialog.querySelector('#mimic-chat-send');
    const cancelBtn = dialog.querySelector('#mimic-chat-cancel');

    function submit() {
      const text = input.value.trim();
      overlay.remove();

      if (!text) return;

      // ── Bongo easter egg trigger ─────────────────────
      if (/bongo/i.test(text)) {
        M.FSM.transitionTo('bongo');
        M.Bubble.show('🥁 Bongo Cat 模式！', { duration: 2000 });
        return;
      }

      // ── Listening (听歌) trigger ──────────────────────
      if (/听歌|music|耳机|来点音乐/i.test(text)) {
        M.FSM.transitionTo('listening');
        M.Bubble.show('🎧 正在听歌… ♪', { duration: 2500 });
        return;
      }

      // ── Stop listening / stop bongo ───────────────────
      if (/停止|stop|别唱了|别敲了/i.test(text)) {
        if (M.FSM.state === 'listening' || M.FSM.state === 'bongo') {
          M.FSM.transitionTo('idle');
          M.Bubble.show('好吧~ (´・ω・`)', { duration: 1500 });
          return;
        }
      }

      // Show user's text briefly as thought bubble
      M.Bubble.show('”' + text + '”', { duration: 800, thought: true });

      // Get response
      setTimeout(() => {
        const reply = getMockReply(text);

        // Try backend API first (async, fallback to mock)
        trySendToBackend(text, reply);
      }, 900);
    }

    sendBtn.addEventListener('click', submit);
    cancelBtn.addEventListener('click', () => overlay.remove());
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submit();
      if (e.key === 'Escape') overlay.remove();
    });

    // Focus input
    setTimeout(() => input.focus(), 50);
  }

  function trySendToBackend(userText, fallbackReply) {
    if (!M.config.apiEnabled) {
      // No backend — use mock
      showReply(fallbackReply);
      return;
    }

    try {
      const payload = JSON.stringify({ message: userText });
      const url = new URL(M.config.backendUrl + '/api/chat');

      const options = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
        },
        timeout: 2000,
      };

      const req = M.http.request(options, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          try {
            const data = JSON.parse(body);
            showReply(data.reply || data.response || fallbackReply);
          } catch (e) {
            showReply(fallbackReply);
          }
        });
      });

      req.on('error', () => {
        showReply(fallbackReply);
      });

      req.write(payload);
      req.end();
    } catch (e) {
      showReply(fallbackReply);
    }
  }

  function showReply(text) {
    // Happy expression for the reply
    M.FSM.transitionTo('happy');
    M.Bubble.show(text, { duration: Math.max(2000, text.length * 80), thought: false });
    setTimeout(() => {
      if (M.FSM.state === 'happy') M.FSM.transitionTo('idle');
    }, Math.max(2000, text.length * 80));
  }

  // ── Keyboard shortcut ─────────────────────────────────────

  function setupChat() {
    window.addEventListener('keydown', (e) => {
      // Enter (when not in input) opens chat
      if (e.key === 'Enter' && document.activeElement === document.body) {
        e.preventDefault();
        showChatInput();
      }
    });

    console.log('[chat] Enter-key chat dialog + mock responses ready');
  }

  M.Interaction = M.Interaction || {};
  M.Interaction.setupChat = setupChat;
})();
