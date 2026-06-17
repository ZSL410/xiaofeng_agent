// ═══════════════════════════════════════════════════════════
//  Mimic v2.0 — Desktop Pet Bootstrapper
//  Articulated pixel character, eating animation, eye tracking,
//  message queue, body-part click reactions, chat dialog.
// ═══════════════════════════════════════════════════════════

const APP_VERSION = '2.5.0';

;(function () {
  const M = window.Mimic;
  M.VERSION = APP_VERSION;

  // ── Bind DOM ─────────────────────────────────────────────
  M.canvas   = document.getElementById('pet-canvas');
  M.ctx      = M.canvas.getContext('2d');
  M.overlay  = document.getElementById('overlay');
  M.bubbleEl = document.getElementById('bubble');

  // ── Animation loop ───────────────────────────────────────
  function tick(now) {
    if (M.FSM) M.FSM.update(now);
    if (M._updateEyeTracking) M._updateEyeTracking();
    if (M.Rendering && M.Rendering.draw) M.Rendering.draw();
    requestAnimationFrame(tick);
  }

  // ── Boot ─────────────────────────────────────────────────
  function boot() {
    console.log('========================================');
    console.log('  拟态 Desktop Pet  v' + APP_VERSION + '  started');
    console.log('  Character: articulated pixel humanoid (16×20 grid)');
    console.log('  Features: eating anim | eye tracking | click react | chat | yawn');
    console.log('  States: idle | working | happy | surprised | alert');
    console.log('  Target dir:', M.TARGET);
    console.log('  Backend API:', M.config.apiEnabled ? M.config.backendUrl : 'disabled');
    console.log('========================================');

    // Init layout + default size
    M.Layout.applySize(80);

    // Setup interactions
    if (M.Interaction) {
      M.Interaction.setupDrag();
      M.Interaction.setupContextMenu();
      M.Interaction.setupDrop();
      M.Interaction.setupEyeTracking();
      M.Interaction.setupClickReactions();
      M.Interaction.setupChat();
      M.Interaction.setupMusicDetect();
    }

    // Start animation
    requestAnimationFrame(tick);
  }

  boot();
})();
