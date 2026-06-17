// ═══════════════════════════════════════════════════════════
//  Mimic v2 — Speech Bubble (message queue, mouth positioning)
//
//  • Messages queued, shown sequentially
//  • Bubble positioned above character MOUTH (not pet top)
//  • Large enough gap so the bubble never covers the face
//  • Tail arrow points down toward the mouth
//  • Thought bubble variant
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  // Signal layout.js not to override bubble positioning
  M._bubbleManaged = true;

  const queue = [];
  let active = null;
  let hideTimer = null;
  let thoughtDots = null;
  let thoughtInterval = null;

  const MIN_DISPLAY = 1800;
  const PER_CHAR_TIME = 50;
  const MOUTH_GAP = 14;  // px between mouth top and bubble bottom

  // ── Show bubble (queued) ─────────────────────────────────

  function show(text, options) {
    const opts = options || {};
    const duration = opts.duration || 0;
    const kaomoji = opts.kaomoji || null;
    const thought = opts.thought || false;

    const fullText = kaomoji ? kaomoji + ' ' + text : text;

    // Deduplicate
    if (queue.length > 0 && queue[queue.length - 1].text === fullText) return;
    if (active && active.text === fullText) return;

    queue.push({ text: fullText, duration, thought });
    if (queue.length > 8) queue.shift();

    processQueue();
  }

  function showImmediate(text, options) {
    clearActive();
    queue.length = 0;
    queue.push({ text: text, duration: (options && options.duration) || 2000, thought: false });
    processQueue();
  }

  function clearActive() {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (thoughtInterval) { clearInterval(thoughtInterval); thoughtInterval = null; }
    if (thoughtDots) { thoughtDots.remove(); thoughtDots = null; }
    const el = M.bubbleEl;
    if (el) { el.classList.remove('on'); el.textContent = ''; }
    active = null;
  }

  function processQueue() {
    if (active || queue.length === 0) return;
    active = queue.shift();
    displayMessage(active);
  }

  function displayMessage(msg) {
    const el = M.bubbleEl;
    if (!el) { active = null; processQueue(); return; }

    // Remove old progress bar
    const oldBar = el.querySelector('.nom-progress');
    if (oldBar) oldBar.remove();

    el.textContent = msg.text;

    // Position bubble ABOVE the character's mouth, with generous gap
    positionBubbleAtMouth(el);

    el.classList.add('on');

    if (msg.thought && !thoughtDots) {
      startThoughtDots(el);
    }

    const autoDuration = Math.max(MIN_DISPLAY, msg.text.length * PER_CHAR_TIME);
    const duration = msg.duration > 0 ? msg.duration : autoDuration;

    hideTimer = setTimeout(() => {
      el.classList.remove('on');
      clearActive();
      M.Layout.updateWindowSizeAndLayout(null);
      setTimeout(() => { active = null; processQueue(); }, 250);
    }, duration);
  }

  // ── Position bubble above character mouth ─────────────────

  function positionBubbleAtMouth(el) {
    const mouthPos = M.mouthCanvasPos || {
      x: M.petCX,
      y: M.petCY - M.petSize * 0.25
    };

    // Styling
    const metrics = M.bubbleMetrics;
    el.style.fontSize   = (metrics.fontSize || 13) + 'px';
    el.style.padding    = (metrics.padV || 6) + 'px ' + (metrics.padH || 12) + 'px';
    el.style.maxWidth   = Math.round(M.winW * 0.85) + 'px';
    el.style.whiteSpace = 'normal';
    el.style.wordWrap   = 'break-word';
    el.style.position   = 'absolute';

    // Horizontal centering
    el.style.left = '50%';
    el.style.transform = 'translateX(-50%)';

    // Vertical: bubble bottom (arrow tip) at MOUTH_GAP px above mouth
    // Measure the bubble's actual height after styling
    const bubbleH = el.offsetHeight || 40;
    el.style.top = Math.max(2, mouthPos.y - bubbleH - MOUTH_GAP) + 'px';

    // Update window size to accommodate the bubble
    M.Layout.updateWindowSizeAndLayout(el.textContent);
  }

  // ── Thought dots ──────────────────────────────────────────

  function startThoughtDots(bubbleEl) {
    thoughtDots = document.createElement('div');
    thoughtDots.className = 'thought-dots';
    thoughtDots.style.cssText = `
      position: absolute;
      pointer-events: none;
      z-index: 11;
      font-size: 20px;
      color: #aaa;
      letter-spacing: 2px;
    `;
    // Position dots above the bubble
    const bubbleTop = parseFloat(bubbleEl.style.top) || 0;
    thoughtDots.style.top  = Math.max(0, bubbleTop - 22) + 'px';
    thoughtDots.style.left = '50%';
    thoughtDots.style.transform = 'translateX(-50%)';
    thoughtDots.textContent = '…';
    bubbleEl.parentNode.appendChild(thoughtDots);

    let dots = 0;
    thoughtInterval = setInterval(() => {
      dots = (dots + 1) % 4;
      if (thoughtDots) thoughtDots.textContent = '.'.repeat(dots || 1);
    }, 400);
  }

  function hide() {
    clearActive();
    queue.length = 0;
    M.Layout.updateWindowSizeAndLayout(null);
  }

  // ── Progress bar (file copy) ──────────────────────────────

  function showProgress(text, fraction) {
    const el = M.bubbleEl;
    if (!el) return;

    if (!active || active.text !== text) {
      // New message
      clearActive();
      active = { text, duration: 0, thought: false };
      el.textContent = text;
      positionBubbleAtMouth(el);
      el.classList.add('on');
    }

    // Ensure progress bar exists
    let bar = el.querySelector('.nom-progress');
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'nom-progress';
      bar.style.cssText = `
        height: 4px;
        background: #eee;
        border-radius: 2px;
        margin-top: 5px;
        overflow: hidden;
      `;
      const fill = document.createElement('div');
      fill.className = 'nom-progress-fill';
      fill.style.cssText = `
        height: 100%;
        background: #FFB347;
        border-radius: 2px;
        transition: width 0.2s ease;
      `;
      bar.appendChild(fill);
      el.appendChild(bar);
    }

    const fill = bar.querySelector('.nom-progress-fill');
    if (fill) fill.style.width = Math.round(Math.max(0, Math.min(1, fraction)) * 100) + '%';

    positionBubbleAtMouth(el);
  }

  // ── Export ────────────────────────────────────────────────

  M.Bubble = {
    show,
    showImmediate,
    showProgress,
    hide,
    positionAtMouth: positionBubbleAtMouth,
  };

  console.log('[bubble v2] message queue + mouth-gap=' + MOUTH_GAP + 'px ready');
})();
