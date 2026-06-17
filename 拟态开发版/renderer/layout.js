// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — Window Layout Engine
//
//  Dynamic window sizing & positioning.
//  Guarantees: pet is ALWAYS centred at (winW/2, winH/2).
//  Bubble fits in the overhead region above the pet.
//
//  Formula (v1.2.0):
//    overhead  = measuredH + ARROW_H + GAP
//    winH      = petSize + 2 × max(overhead, BOTTOM_MARGIN)
//    petCX = winW/2, petCY = winH/2
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  // ── Bubble metrics (font / padding scale with petSize) ──

  function updateBubbleMetrics() {
    const ps = M.petSize;
    const fontSize = Math.max(8, Math.min(16, Math.round(ps * 0.15)));
    const padV = Math.max(3, Math.round(fontSize * 0.55));
    const padH = Math.max(5, Math.round(fontSize * 0.9));
    M.bubbleMetrics = { fontSize, padV, padH };
  }

  // ── Offscreen measurement ───────────────────────────────

  function measureBubbleSize(text) {
    const { fontSize, padV, padH } = M.bubbleMetrics;
    const maxW = Math.round((M.petSize + M.SIDE_PAD * 2) * 0.8);

    const m = document.createElement('div');
    m.style.position        = 'absolute';
    m.style.visibility      = 'hidden';
    m.style.pointerEvents   = 'none';
    m.style.fontFamily      = 'sans-serif';
    m.style.fontSize        = fontSize + 'px';
    m.style.padding         = padV + 'px ' + padH + 'px';
    m.style.maxWidth        = maxW + 'px';
    m.style.whiteSpace      = 'normal';
    m.style.wordWrap        = 'break-word';
    m.style.border          = '1px solid #999';
    m.style.borderRadius    = '14px';
    m.style.backgroundColor = 'rgba(255,255,255,0.94)';
    m.textContent           = text;
    document.body.appendChild(m);

    const w = m.offsetWidth;
    const h = m.offsetHeight;
    document.body.removeChild(m);

    return { w, h };
  }

  // ── Main layout computation ─────────────────────────────

  function updateWindowSizeAndLayout(bubbleText) {
    const { SIDE_PAD, ARROW_H, GAP, BOTTOM_MARGIN } = M;

    // 1. Measure bubble text
    let measuredH = 0;
    if (bubbleText) {
      const sz = measureBubbleSize(bubbleText);
      measuredH = sz.h;
    }
    M.currentBubbleH = measuredH;

    // 2. Compute window dimensions
    M.winW = M.petSize + SIDE_PAD * 2;

    const overhead = measuredH > 0 ? (measuredH + ARROW_H + GAP) : 0;
    M.winH = M.petSize + 2 * Math.max(overhead, BOTTOM_MARGIN);

    // 3. Pet centre = window centre
    M.petCX = M.winW / 2;
    M.petCY = M.winH / 2;

    const wPx = M.winW + 'px';
    const hPx = M.winH + 'px';

    // 4. Resize canvas & DOM
    const canvas = M.canvas;
    if (canvas) {
      canvas.width  = M.winW;
      canvas.height = M.winH;
      canvas.style.width  = wPx;
      canvas.style.height = hPx;
    }

    document.body.style.width  = wPx;
    document.body.style.height = hPx;
    document.documentElement.style.width  = wPx;
    document.documentElement.style.height = hPx;

    const overlay = M.overlay;
    if (overlay) {
      overlay.style.width  = wPx;
      overlay.style.height = hPx;
    }

    // 5. Position bubble — delegate to Bubble module if active,
    //    otherwise use default pet-top positioning
    if (bubbleText) {
      const { fontSize, padV, padH } = M.bubbleMetrics;
      const maxW = Math.round(M.winW * 0.8);

      const bubbleEl = M.bubbleEl;
      if (bubbleEl) {
        bubbleEl.style.fontSize   = fontSize + 'px';
        bubbleEl.style.padding    = padV + 'px ' + padH + 'px';
        bubbleEl.style.maxWidth   = maxW + 'px';
        bubbleEl.style.whiteSpace = 'normal';
        bubbleEl.style.wordWrap   = 'break-word';

        // Only set top if Bubble module is NOT managing positioning
        if (!M._bubbleManaged) {
          const petTop = M.petCY - M.petSize / 2;
          const arrowTipY = petTop - GAP;
          bubbleEl.style.top = (arrowTipY - measuredH) + 'px';
        }
      }
    }

    // 6. Tell main process to resize (type-safe, flat args)
    const sendW = Number(M.winW);
    const sendH = Number(M.winH);
    if (!isNaN(sendW) && !isNaN(sendH) && sendW > 0 && sendH > 0) {
      M.ipc.send('resize-window', sendW, sendH);
    }

    // 7. Redraw
    if (M.Rendering && M.Rendering.draw) {
      M.Rendering.draw();
    }

    console.log('[layout] pet=' + M.petSize +
                ' bubbleH=' + measuredH +
                ' overhead=' + overhead +
                ' => win=' + M.winW + 'x' + M.winH +
                ' petC=(' + Math.round(M.petCX) + ',' + Math.round(M.petCY) + ')' +
                ' petTop=' + Math.round(M.petCY - M.petSize / 2));
  }

  // ── Size control (called when user changes pet size) ────

  function applySize(size) {
    // Type guard
    const s = Number(size);
    if (isNaN(s) || s <= 0) {
      console.warn('[layout] applySize rejected: invalid size', size);
      return;
    }
    size = s;

    // Smooth scale transition
    const oldSize = M.petSize;
    M.petSize = size;
    updateBubbleMetrics();

    // Trigger scale animation
    if (M.Anim) {
      M.Anim.targetScale = 1;
      M.Anim.bodyScale = oldSize / size;  // start from old scale
    }

    M.ipc.send('set-pet-size', M.petSize);

    const bubbleEl = M.bubbleEl;
    if (bubbleEl && bubbleEl.classList.contains('on') && bubbleEl.textContent) {
      updateWindowSizeAndLayout(bubbleEl.textContent);
    } else {
      updateWindowSizeAndLayout(null);
    }

    M.lastBlink = performance.now();
    M.eyeOpen = true;
  }

  // ── Export ──────────────────────────────────────────────

  M.Layout = {
    updateBubbleMetrics,
    measureBubbleSize,
    updateWindowSizeAndLayout,
    applySize,
  };

  console.log('[layout] module initialized');
})();
