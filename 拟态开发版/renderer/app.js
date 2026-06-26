// ═══════════════════════════════════════════════════════════
//  Mimic v3.7.1 — Desktop Pet Bootstrapper
//  Articulated pixel character, eating animation, eye tracking,
//  message queue, body-part click reactions, chat dialog,
//  low-power idle mode (15fps after 30s inactivity).
// ═══════════════════════════════════════════════════════════

const APP_VERSION = '3.7.1';

;(function () {
  const M = window.Mimic;
  M.VERSION = APP_VERSION;

  // ── Bind DOM ─────────────────────────────────────────────
  M.canvas   = document.getElementById('pet-canvas');
  M.ctx      = M.canvas.getContext('2d');
  M.overlay  = document.getElementById('overlay');
  M.bubbleEl = document.getElementById('bubble');

  // ── Low-power idle mode state ────────────────────────────
  M._idleFrameCounter = 0;
  M._idleLowPower = false;          // true when running at 15fps
  M._idleLowPowerThreshold = 30000; // 30s no mouse → drop to 15fps
  M._idleLowPowerFps = 15;          // target fps in low-power mode
  M._idleLowPowerSkip = Math.round(60 / 15); // skip every N frames (4)

  // ── Animation loop ───────────────────────────────────────
  function tick(now) {
    if (M.FSM) M.FSM.update(now);
    if (M._updateEyeTracking) M._updateEyeTracking();

    // Low-power idle mode: frame skipping
    const idleTime = now - (M.lastActivity || now);
    const isIdle = M.FSM && M.FSM.state === 'idle';

    if (isIdle && idleTime >= M._idleLowPowerThreshold) {
      // Enter low-power: draw at 15fps via frame counter skip
      M._idleLowPower = true;
      M._idleFrameCounter++;
      if (M._idleFrameCounter >= M._idleLowPowerSkip) {
        M._idleFrameCounter = 0;
        if (M.Rendering && M.Rendering.draw) M.Rendering.draw();
      }
    } else {
      // Full speed: draw every frame
      if (M._idleLowPower) {
        // Just exited low-power mode — reset counter
        M._idleFrameCounter = 0;
      }
      M._idleLowPower = false;
      if (M.Rendering && M.Rendering.draw) M.Rendering.draw();
    }

    requestAnimationFrame(tick);
  }

  // ── Boot ─────────────────────────────────────────────────
  function boot() {
    console.log('========================================');
    console.log('  拟态 Desktop Pet  v' + APP_VERSION + '  started');
    console.log('  Character: articulated pixel humanoid (16×20 grid)');
    console.log('  Features: eating anim | eye tracking | click react | chat | yawn');
    console.log('  States: idle | working | happy | surprised | alert');
    console.log('  Low-power: 15fps after 30s idle | click-to-react | eye-follow');
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
