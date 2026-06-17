// ═══════════════════════════════════════════════════════════
//  Mimic — FSM State: happy
//  Success. Bounce-in with arm wave. Auto-return 2s.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const happyHandler = {
    enter() {
      M.eyeOpen = true;
      M.FSM._stopWorkAnim();

      const A = M.Anim;
      A.mouthOpen = 0;
      A.headTilt = 0;
      A.leftArm = { dx: -1, dy: -4 };
      A.rightArm = { dx: 1, dy: -4 };
      A.targetScale = 1;

      // Audio + particles
      if (M.Audio) M.Audio.play('chirp');
      if (M.Particles) {
        M.Particles.burst('heart', 6, { x: 8, y: 2 });
        M.Particles.burst('note', 3, { x: 8, y: 1 });  // 🎵 happy notes
      }

      M.Rendering.draw();

      M.FSM._data.happyTimer = setTimeout(() => {
        if (M.FSM.state === 'happy') M.FSM.transitionTo('idle');
      }, 2000);
    },

    update(now) {
      const A = M.Anim;
      const elapsed = now - M.FSM.stateStartTime;

      // Bounce-in (smooth lerp handles transition from previous bobOffset)
      const t = Math.min(elapsed / 500, 1);
      A.bobOffset = Math.sin(t * Math.PI * 1.5) * 6 * (1 - t);

      // Body squash/stretch
      if (t < 0.3) A.bodySquash = -0.1;
      else if (t < 0.5) A.bodySquash = 0.08;
      else A.bodySquash *= 0.9;

      // Arm wave
      A.rightArm.dx = 1 + Math.sin(elapsed * 0.08) * 0.5;
    },

    exit() {
      const A = M.Anim;
      A.leftArm = { dx: 0, dy: 0 };
      A.rightArm = { dx: 0, dy: 0 };
      A.targetScale = 1;
      // bobOffset NOT reset — smooth lerp handles it
      M.FSM._clearTimer('happyTimer');
    },

    expression: 'happy',
  };

  M.FSM._handlers.happy = happyHandler;
  console.log('[fsm/states/happy] v2 — bounce + wave (smooth transitions)');
})();
