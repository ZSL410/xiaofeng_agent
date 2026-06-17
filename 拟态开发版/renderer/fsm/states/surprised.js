// ═══════════════════════════════════════════════════════════
//  Mimic — FSM State: surprised
//  Error. Arms out, wide eyes, shake. Auto-return 2.5s.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const surprisedHandler = {
    enter() {
      M.eyeOpen = true;
      M.FSM._stopWorkAnim();

      const A = M.Anim;
      A.mouthOpen = 0.8;
      A.leftArm = { dx: -2, dy: -1 };
      A.rightArm = { dx: 2, dy: -1 };
      A.headTilt = 0;
      A.eyePupilX = 0;
      A.eyePupilY = 0;
      A.targetScale = 1;

      if (M.Audio) M.Audio.play('boing');

      M.Rendering.draw();

      M.FSM._data.surprisedTimer = setTimeout(() => {
        if (M.FSM.state === 'surprised') M.FSM.transitionTo('idle');
      }, 2500);
    },

    update(now) {
      const A = M.Anim;
      const elapsed = now - M.FSM.stateStartTime;

      if (elapsed < 400) {
        const intensity = (400 - elapsed) / 400;
        A.bobOffset = Math.sin(elapsed * 0.06) * 3 * intensity;
        A.headTilt = Math.sin(elapsed * 0.08) * 0.5 * intensity;
      } else {
        A.bobOffset = 0;
        A.headTilt = 0;
        const settle = Math.min(1, (elapsed - 400) / 800);
        A.leftArm.dx = -2 * (1 - settle);
        A.rightArm.dx = 2 * (1 - settle);
        A.leftArm.dy = -1 * (1 - settle);
        A.rightArm.dy = -1 * (1 - settle);
        A.mouthOpen = 0.8 * (1 - settle);
      }
    },

    exit() {
      const A = M.Anim;
      A.leftArm = { dx: 0, dy: 0 };
      A.rightArm = { dx: 0, dy: 0 };
      A.headTilt = 0;
      M.FSM._clearTimer('surprisedTimer');
      // bobOffset NOT reset — smooth lerp handles transition
    },

    expression: 'surprised',
  };

  M.FSM._handlers.surprised = surprisedHandler;
  console.log('[fsm/states/surprised] v2 — shake + arms out (smooth transitions)');
})();
