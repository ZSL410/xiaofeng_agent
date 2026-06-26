// ═══════════════════════════════════════════════════════════
//  Mimic v3.7.1 — Body Part Click Reactions
//
//  Click head → happy state (bounce + smile) → 1.5s → idle
//  Click body → surprised state (wide eyes + open mouth) → 1.5s → idle
//  Double-click → spin + spark particles + 2.5s happy
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let lastClickTime = 0;
  let clickCount = 0;

  function setupClickReactions() {
    const canvas = M.canvas;
    if (!canvas) return;

    canvas.addEventListener('click', (e) => {
      M.lastActivity = performance.now();

      const now = performance.now();
      if (now - lastClickTime < 400) {
        clickCount++;
      } else {
        clickCount = 1;
      }
      lastClickTime = now;

      // Determine which body part was clicked
      const part = hitTest(e.offsetX, e.offsetY);
      const A = M.Anim;

      if (clickCount >= 2) {
        // Double-click: spin + happy
        handleDoubleClick(A);
        return;
      }

      switch (part) {
        case 'head':
          handleHeadClick(A);
          break;
        case 'body':
          handleBodyClick(A);
          break;
        default:
          // Clicked elsewhere — gentle boop
          handleBoop(A);
      }
    });

    console.log('[click] body part click reactions registered');
  }

  // ── Hit testing ───────────────────────────────────────────

  function hitTest(canvasX, canvasY) {
    const cellSize = Math.max(1, Math.round(M.petSize / 20));
    const totalW = 16 * cellSize;
    const totalH = 20 * cellSize;
    const gx = M.petCX - totalW / 2;
    const gy = M.petCY - totalH / 2 + (M.Anim.bobOffset || 0);

    // Convert canvas coords to grid coords
    const col = (canvasX - gx) / cellSize;
    const row = (canvasY - gy) / cellSize;

    // Head region: cols 4-11, rows 0-6
    if (col >= 4 && col <= 11 && row >= 0 && row <= 6) return 'head';
    // Body region: cols 5-10, rows 7-13
    if (col >= 5 && col <= 10 && row >= 7 && row <= 13) return 'body';

    return 'other';
  }

  // ── Reactions ─────────────────────────────────────────────

  function handleHeadClick(A) {
    // Smile + head tilt
    A.headTilt = -0.6;
    setTimeout(() => { A.headTilt = 0; }, 400);
    // Small bounce
    A.bobOffset = -4;
    setTimeout(() => { A.bobOffset = 0; }, 200);
    // Audio + note particles
    if (M.Audio) M.Audio.play('chirp');
    if (M.Particles) M.Particles.burst('note', 3, { x: 8, y: 3 });
    // Blush
    M.Bubble.show('好痒~  (*/ω＼*)', { duration: 1500, thought: false });
    // Briefly change to happy
    if (M.FSM.state === 'idle') {
      M.FSM.transitionTo('happy');
      setTimeout(() => { if (M.FSM.state === 'happy') M.FSM.transitionTo('idle'); }, 1500);
    }
  }

  function handleBodyClick(A) {
    // v3.7.1: body click → surprised state (wide eyes + open mouth)
    A.bobOffset = -4;
    A.bodySquash = -0.1;
    setTimeout(() => { A.bobOffset = 0; A.bodySquash = 0; }, 300);
    if (M.Audio) M.Audio.play('boing');
    if (M.Particles) M.Particles.burst('spark', 4, { x: 8, y: 7 });
    M.Bubble.show('嘿！别戳我肚子！', { duration: 1500, thought: false });
    // Transition to surprised, auto-recover after 1.5s
    if (M.FSM.state === 'idle') {
      M.FSM.transitionTo('surprised');
      setTimeout(() => {
        if (M.FSM.state === 'surprised') M.FSM.transitionTo('idle');
      }, 1500);
    }
  }

  function handleDoubleClick(A) {
    // Exaggerated bounce + sparkle
    A.bobOffset = -12;
    A.bodySquash = -0.2;
    A.rightArm = { dx: 2, dy: -5 };
    setTimeout(() => {
      A.bobOffset = 0;
      A.bodySquash = 0;
      A.rightArm = { dx: 0, dy: 0 };
    }, 500);
    if (M.Audio) M.Audio.play('boing');
    if (M.Particles) M.Particles.burst('spark', 10, { x: 8, y: 6 });
    M.Bubble.show('(*´▽`*) 你戳我干嘛~', { duration: 2000, thought: false });
    M.FSM.transitionTo('happy');
    setTimeout(() => { if (M.FSM.state === 'happy') M.FSM.transitionTo('idle'); }, 2500);
  }

  function handleBoop(A) {
    A.bobOffset = -2;
    setTimeout(() => { A.bobOffset = 0; }, 150);
  }

  M.Interaction = M.Interaction || {};
  M.Interaction.setupClickReactions = setupClickReactions;
})();
