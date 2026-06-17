// ═══════════════════════════════════════════════════════════
//  Mimic v2.4 — Music Detection Renderer
//
//  Listens for 'music-playing' / 'music-stopped' IPC from main.
//  Auto-transitions to 'listening' when music is detected and
//  the pet is in idle state (not user-triggered).
//  Auto-returns to idle when music stops (auto-triggered only).
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let autoListening = false; // true = entered listening via auto-detect

  function setupMusicDetect() {
    if (!M.ipc) return;

    M.ipc.on('music-playing', (_event, playerName) => {
      console.log('[music-detect] renderer got music-playing:', playerName);

      // Only auto-trigger if currently idle (not user in another state)
      if (M.FSM && M.FSM.state === 'idle') {
        M.FSM.transitionTo('listening', { source: 'auto', player: playerName });
        autoListening = true;
        if (M.Bubble) {
          M.Bubble.show('🎧 检测到音乐~ (' + (playerName || '?') + ')', { duration: 2500 });
        }
      }
    });

    M.ipc.on('music-stopped', () => {
      console.log('[music-detect] renderer got music-stopped');

      // Only exit if we auto-entered (user-triggered listening is respected)
      if (autoListening && M.FSM && M.FSM.state === 'listening') {
        M.FSM.transitionTo('idle');
        autoListening = false;
      }
    });

    // When user manually triggers listening via chat, mark as non-auto
    // (the chat.js transition won't set autoListening)
    const origTransition = M.FSM.transitionTo;
    M.FSM.transitionTo = function (state, ctx) {
      // If transitioning to listening from chat (not from music-detect),
      // mark as manual so music-stopped won't kill it
      if (state === 'listening' && (!ctx || ctx.source !== 'auto')) {
        autoListening = false;
      }
      return origTransition.call(this, state, ctx);
    };

    console.log('[music-detect] auto-detect listener registered');
  }

  M.Interaction = M.Interaction || {};
  M.Interaction.setupMusicDetect = setupMusicDetect;
})();
