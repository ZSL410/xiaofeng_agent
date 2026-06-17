// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — Window Drag (document-level tracking)
//
//  Tracks pointer on document for reliable drag even when
//  cursor leaves the canvas. No setPointerCapture (avoids
//  WSL2/Electron compat issues). Sends clean integer deltas.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let dragging = false;
  let dragSX = 0, dragSY = 0;

  function setupDrag() {
    const canvas = M.canvas;
    if (!canvas) {
      console.warn('[drag] canvas not available');
      return;
    }

    // ── Start drag on canvas pointerdown ────────────────────
    canvas.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      dragging = true;
      dragSX = Math.round(Number(e.screenX));
      dragSY = Math.round(Number(e.screenY));
      if (isNaN(dragSX)) dragSX = 0;
      if (isNaN(dragSY)) dragSY = 0;
      M.lastActivity = performance.now();
    });

    // ── Track movement on document (not canvas) ─────────────
    document.addEventListener('pointermove', (e) => {
      if (!dragging) return;

      const sx = Math.round(Number(e.screenX));
      const sy = Math.round(Number(e.screenY));
      if (isNaN(sx) || isNaN(sy)) return;

      let dx = sx - dragSX;
      let dy = sy - dragSY;
      if (isNaN(dx)) dx = 0;
      if (isNaN(dy)) dy = 0;

      dragSX = sx;
      dragSY = sy;

      if (dx !== 0 || dy !== 0) {
        try {
          M.ipc.send('window-drag', dx, dy);
        } catch (err) {
          console.error('[drag] IPC send error:', err.message);
        }
      }
    });

    // ── End drag on document pointerup ──────────────────────
    document.addEventListener('pointerup', () => {
      if (!dragging) return;
      dragging = false;
      M.ipc.send('save-position');
    });
  }

  M.Interaction = M.Interaction || {};
  M.Interaction.setupDrag = setupDrag;
  console.log('[drag] document-level drag + position save registered');
})();
