// ═══════════════════════════════════════════════════════════
//  Mimic v2.3 — FSM State: listening (听歌)
//
//  Triggered by chat keywords: "听歌" / "music" / "耳机"
//  Character wears headphones, closes eyes, sways gently,
//  with music notes floating continuously.
//  Exits on mouse activity → idle, or via "停止" / "stop".
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let noteSpawnTimer = 0;
  let headphoneSway = 0;

  const listeningHandler = {
    enter() {
      M.eyeOpen = false;
      M.FSM._stopWorkAnim();
      M.FSM._clearTimers();

      const A = M.Anim;
      A.mouthOpen = 0;
      A.headTilt = 0;
      A.leftArm = { dx: 0, dy: -2 };   // arms relaxed up (holding headphones?)
      A.rightArm = { dx: 0, dy: -2 };
      A.leftLeg = { dx: 0, dy: 0 };
      A.rightLeg = { dx: 0, dy: 0 };
      A.feetY = 0;
      A.yawnPhase = 0;
      A.targetScale = 1;
      A.bodySquash = 0;
      A.showHeadphones = true;   // 🎧

      noteSpawnTimer = performance.now();
      headphoneSway = 0;

      // Initial burst of notes
      if (M.Particles) M.Particles.burst('note', 5, { x: 8, y: 1 });

      // Show player info if auto-detected
      const player = (M.FSM.context && M.FSM.context.player);
      if (player && M.Bubble && M.FSM.context.source === 'auto') {
        M.Bubble.show('🎧 ' + player, { duration: 2000 });
      }

      M.Rendering.draw();

      console.log('[fsm/states/listening] entered — 🎧 听歌模式',
                  player ? '(' + player + ')' : '');
    },

    update(now) {
      const A = M.Anim;
      const elapsed = now - M.FSM.stateStartTime;

      // ── Gentle body sway (side-to-side) ──────────────────
      headphoneSway = Math.sin(elapsed * 0.002) * 0.35;

      // ── Breathing bob (gentle) ──────────────────────────
      A.bobOffset = Math.sin(elapsed * 0.001) * 1.2;

      // ── Head tilt follows sway ──────────────────────────
      A.headTilt = headphoneSway;

      // ── Gentle body squash ──────────────────────────────
      A.bodySquash = Math.sin(elapsed * 0.0025) * 0.04;

      // ── Arms sway with body ─────────────────────────────
      A.leftArm.dx = headphoneSway * 1.5;
      A.leftArm.dy = -2 + Math.sin(elapsed * 0.003) * 0.8;
      A.rightArm.dx = -headphoneSway * 1.5;
      A.rightArm.dy = -2 + Math.sin(elapsed * 0.003 + 1) * 0.8;

      // ── Eyes stay closed ────────────────────────────────
      M.eyeOpen = false;

      // ── Spawn note particles every ~600ms ────────────────
      const sinceNote = now - noteSpawnTimer;
      if (sinceNote > 600 && M.Particles) {
        noteSpawnTimer = now;
        M.Particles.burst('note', 1, {
          x: 6 + Math.random() * 4,
          y: -1 - Math.random() * 2,
        });
      }

      // ── Exit: mouse activity returns to idle ────────────
      const idleTime = now - (M.lastActivity || now);
      if (idleTime < 300) {
        // User moved mouse recently — keep listening
        // (don't exit immediately on mouse activity)
      }
    },

    exit() {
      const A = M.Anim;
      A.showHeadphones = false;
      A.headTilt = 0;
      A.leftArm = { dx: 0, dy: 0 };
      A.rightArm = { dx: 0, dy: 0 };
      A.bodySquash = 0;
      M.eyeOpen = true;
      M.FSM._clearTimers();
      console.log('[fsm/states/listening] exited');
    },

    expression: 'listening',
  };

  M.FSM._handlers.listening = listeningHandler;
  console.log('[fsm/states/listening] initialized — trigger: 听歌 / music / 耳机');
})();
