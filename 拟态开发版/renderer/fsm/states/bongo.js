// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — FSM State: bongo (Easter Egg)
//
//  Triggered by typing "bongo" in chat.
//  Character sits and alternately taps hands like a cat
//  playing bongos. Auto-returns to idle after 10s inactivity.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let lastTapTime = 0;
  let tapSide = 0; // 0 = left, 1 = right
  let tapCount = 0;

  const bongoHandler = {
    enter() {
      M.eyeOpen = true;
      M.FSM._stopWorkAnim();
      M.FSM._clearTimers();

      const A = M.Anim;
      // Sit pose: legs bent
      A.leftLeg = { dx: 0, dy: 2 };
      A.rightLeg = { dx: 0, dy: 2 };
      A.feetY = 2;
      A.headTilt = 0.2;
      A.mouthOpen = 0;
      A.bodySquash = -0.05;
      A.eyePupilX = 0;
      A.eyePupilY = -0.5;
      A.targetScale = 1;
      A.bobOffset = 0;

      // Arms ready to tap
      A.leftArm = { dx: -2, dy: 2 };
      A.rightArm = { dx: 2, dy: 2 };

      lastTapTime = performance.now();
      tapSide = 0;
      tapCount = 0;

      // Particles burst
      if (M.Particles) M.Particles.burst('star', 5, { x: 7.5, y: 3 });

      M.Rendering.draw();

      // Auto-return after 10s
      M.FSM._data.bongoTimer = setTimeout(() => {
        if (M.FSM.state === 'bongo') M.FSM.transitionTo('idle');
      }, 10000);
    },

    update(now) {
      const A = M.Anim;
      const elapsed = now - M.FSM.stateStartTime;

      // Gentle body sway
      A.headTilt = Math.sin(elapsed * 0.004) * 0.3;
      A.bobOffset = Math.sin(elapsed * 0.006) * 1;

      // ── Bongo tapping cycle (~180ms per tap) ────────────
      const sinceTap = now - lastTapTime;
      const tapInterval = 180;

      if (sinceTap >= tapInterval) {
        lastTapTime = now;
        tapSide = 1 - tapSide; // alternate
        tapCount++;

        // Play tap sound
        if (M.Audio && tapCount > 0) {
          M.Audio.play('bongo');
        }

        // Note particles every 4 taps
        if (M.Particles && tapCount > 0 && tapCount % 4 === 0) {
          M.Particles.burst('note', 2, { x: 8 + Math.random() * 4, y: 1 });
        }
      }

      // Animate arms based on tap phase
      const phase = Math.min(1, sinceTap / tapInterval);

      if (tapSide === 0) {
        // Left arm taps down
        A.leftArm.dx = -2;
        A.leftArm.dy = 2 + Math.sin(phase * Math.PI) * 4;
        A.rightArm.dx = 2 + Math.sin(phase * Math.PI) * 0.5;
        A.rightArm.dy = -2;
      } else {
        // Right arm taps down
        A.rightArm.dx = 2;
        A.rightArm.dy = 2 + Math.sin(phase * Math.PI) * 4;
        A.leftArm.dx = -2 - Math.sin(phase * Math.PI) * 0.5;
        A.leftArm.dy = -2;
      }
    },

    exit() {
      const A = M.Anim;
      A.leftArm = { dx: 0, dy: 0 };
      A.rightArm = { dx: 0, dy: 0 };
      A.leftLeg = { dx: 0, dy: 0 };
      A.rightLeg = { dx: 0, dy: 0 };
      A.feetY = 0;
      A.headTilt = 0;
      A.bodySquash = 0;
      A.eyePupilX = 0;
      A.eyePupilY = 0;
      M.FSM._clearTimer('bongoTimer');
    },

    expression: 'bongo',
  };

  M.FSM._handlers.bongo = bongoHandler;
  console.log('[fsm/states/bongo] easter egg — type "bongo" to activate');
})();
