// ═══════════════════════════════════════════════════════════
//  Mimic v2 — Eye Tracking
//
//  Pupils follow the mouse cursor. Binds to document for
//  reliable event capture in frameless Electron windows.
//  Smooth lerp avoids jitter. Active only in idle state.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let mouseX = 0, mouseY = 0;
  let mouseOnWindow = false;

  function setupEyeTracking() {
    // Use document + window to catch all mouse events
    document.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseleave', () => { mouseOnWindow = false; });
    window.addEventListener('mouseenter', () => { mouseOnWindow = true; });

    function onMouseMove(e) {
      // clientX/Y are relative to the window content area
      mouseX = e.clientX;
      mouseY = e.clientY;
      mouseOnWindow = true;
      M.lastActivity = performance.now();
    }

    // Per-frame pupil update (called from animation tick)
    M._updateEyeTracking = function () {
      const A = M.Anim;
      if (!A) return;

      if (!mouseOnWindow || M.FSM.state !== 'idle') {
        // Return pupils to center
        A.eyePupilX += (0 - A.eyePupilX) * 0.12;
        A.eyePupilY += (0 - A.eyePupilY) * 0.12;
        return;
      }

      // Window centre
      const cx = M.winW / 2;
      const cy = M.winH / 2;
      const maxDist = Math.max(M.winW, 50) * 0.6;

      const dx = (mouseX - cx) / maxDist;
      const dy = (mouseY - cy) / maxDist;

      // Clamp to [-1, 1] and smooth-follow
      const tx = Math.max(-1, Math.min(1, dx));
      const ty = Math.max(-1, Math.min(1, dy));

      A.eyePupilX += (tx - A.eyePupilX) * 0.25;
      A.eyePupilY += (ty - A.eyePupilY) * 0.25;
    };

    console.log('[eye-tracking] document+window mousemove tracking active');
  }

  M.lastActivity = performance.now();

  M.Interaction = M.Interaction || {};
  M.Interaction.setupEyeTracking = setupEyeTracking;
})();
