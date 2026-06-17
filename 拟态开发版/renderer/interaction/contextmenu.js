// ═══════════════════════════════════════════════════════════
//  Mimic — Right-Click Context Menu
//
//  Opens main-process context menu for size switching.
//  Listens for size-changed events from main process.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  function setupContextMenu() {
    const canvas = M.canvas;
    if (!canvas) {
      console.warn('[contextmenu] canvas not available');
      return;
    }

    canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      M.ipc.send('show-context-menu', M.petSize, M.VERSION);
    });

    // Receive size change from main process (after menu selection, type-safe)
    M.ipc.on('size-changed', (_event, size) => {
      const s = Number(size);
      if (isNaN(s) || s <= 0) return;
      console.log('[size] changing to', s);
      M.Layout.applySize(s);
    });

    console.log('[contextmenu] right-click handler registered');
  }

  M.Interaction = M.Interaction || {};
  M.Interaction.setupContextMenu = setupContextMenu;
})();
