// ═══════════════════════════════════════════════════════════
//  Mimic v3.7.1 — Enhanced Eye Tracking
//
//  Pupils follow the mouse cursor relative to the character's
//  head centre (not window centre). Smooth lerp avoids jitter.
//  Pupil offset clamped to stay within head boundary.
//  Active only in idle state. Mouse leave → return to centre.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let mouseX = 0, mouseY = 0;
  let mouseOnWindow = false;

  // ── Head boundary in grid coords (16×20 grid) ────────────
  // Head occupies cols 4-11, rows 0-6.
  // Left eye centre:  (col 5.5, row 2.5)
  // Right eye centre: (col 9.5, row 2.5)
  // Max pupil displacement (grid cells): ±1.5 horizontal, ±1 vertical
  const PUPIL_MAX_DX = 1.5;  // max horizontal offset in cells
  const PUPIL_MAX_DY = 1.0;  // max vertical offset in cells
  const LERP_SPEED = 0.28;   // smooth follow speed

  function setupEyeTracking() {
    // Use document + window to catch all mouse events
    document.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseleave', onMouseLeave);
    window.addEventListener('mouseenter', onMouseEnter);

    function onMouseMove(e) {
      mouseX = e.clientX;
      mouseY = e.clientY;
      mouseOnWindow = true;
      M.lastActivity = performance.now();
    }

    function onMouseLeave() {
      mouseOnWindow = false;
    }

    function onMouseEnter() {
      mouseOnWindow = true;
    }

    // Per-frame pupil update (called from animation tick)
    M._updateEyeTracking = function () {
      const A = M.Anim;
      if (!A) return;

      if (!mouseOnWindow || M.FSM.state !== 'idle') {
        // Return pupils to centre
        A.eyePupilX += (0 - A.eyePupilX) * 0.12;
        A.eyePupilY += (0 - A.eyePupilY) * 0.12;
        return;
      }

      // ── Compute head centre position on canvas ──────────────
      // Head is drawn at grid rows 0-6, cols 4-11
      // Head centre in grid: col (4+11)/2=7.5, row (0+6)/2=3
      const petSize = M.petSize;
      const cellSize = Math.max(2, Math.round(petSize / 20));
      const totalW = 16 * cellSize;
      const totalH = 20 * cellSize;
      const bob = M.Anim._smoothBob || 0;

      const gx = M.petCX - totalW / 2;
      const gy = M.petCY - totalH / 2 + bob;
      const headCX = gx + 7.5 * cellSize;  // head centre X on canvas
      const headCY = gy + 3.0 * cellSize;   // head centre Y on canvas

      // ── Compute offset from head centre to mouse ────────────
      const dx = mouseX - headCX;
      const dy = mouseY - headCY;

      // Normalize: map screen distance to pupil offset range
      // Use a sensitivity radius proportional to pet size
      const sensitivityRadius = Math.max(petSize * 1.2, 60);

      const rawPX = dx / sensitivityRadius;
      const rawPY = dy / sensitivityRadius;

      // Clamp to head boundary
      const tx = Math.max(-PUPIL_MAX_DX, Math.min(PUPIL_MAX_DX, rawPX));
      const ty = Math.max(-PUPIL_MAX_DY, Math.min(PUPIL_MAX_DY, rawPY));

      // Smooth lerp
      A.eyePupilX += (tx - A.eyePupilX) * LERP_SPEED;
      A.eyePupilY += (ty - A.eyePupilY) * LERP_SPEED;
    };

    console.log('[eye-tracking v3.7.1] head-centre tracking, max offset ±' +
                PUPIL_MAX_DX + '×' + PUPIL_MAX_DY + ' cells');
  }

  M.lastActivity = performance.now();

  M.Interaction = M.Interaction || {};
  M.Interaction.setupEyeTracking = setupEyeTracking;
})();
