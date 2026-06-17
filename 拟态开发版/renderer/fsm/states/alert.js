// ═══════════════════════════════════════════════════════════
//  Mimic — FSM State: alert
//  Reminder. Jumping + pointing arm + sparkle eyes. Permanent.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const alertHandler = {
    enter() {
      M.eyeOpen = true;
      M.FSM._stopWorkAnim();

      const A = M.Anim;
      A.mouthOpen = 0;
      A.leftArm = { dx: 0, dy: 0 };
      A.rightArm = { dx: 2, dy: -6 };
      A.headTilt = -0.2;
      A.eyePupilX = 0;
      A.eyePupilY = -1;
      A.targetScale = 1;

      // Star particles
      if (M.Particles) M.Particles.burst('star', 6, { x: 8, y: 3 });

      M.FSM._data.alertLastJump = performance.now();
      M.Rendering.draw();
    },

    update(now) {
      const A = M.Anim;
      const since = now - (M.FSM._data.alertLastJump || now);

      if (since > 1500) {
        M.FSM._data.alertLastJump = now;
      }

      const t = Math.min(since / 600, 1);

      if (t < 0.2) {
        A.bobOffset = 3;
        A.bodySquash = -0.15;
        A.feetY = 2;
      } else if (t < 0.4) {
        const jt = (t - 0.2) / 0.2;
        A.bobOffset = -10 * jt;
        A.bodySquash = 0.1;
        A.feetY = -3 * jt;
        A.rightArm.dx = 2 + Math.sin(now * 0.02) * 0.4;
      } else if (t < 0.6) {
        const ft = (t - 0.4) / 0.2;
        A.bobOffset = -10 + 10 * ft;
        A.bodySquash = 0.1 - 0.2 * ft;
        A.feetY = -3 + 3 * ft;
      } else {
        A.bobOffset = 0;
        A.bodySquash = 0;
        A.feetY = 0;
      }
    },

    exit() {
      const A = M.Anim;
      A.bodySquash = 0;
      A.feetY = 0;
      A.leftArm = { dx: 0, dy: 0 };
      A.rightArm = { dx: 0, dy: 0 };
      A.headTilt = 0;
      A.eyePupilX = 0;
      A.eyePupilY = 0;
      // bobOffset NOT reset — smooth lerp handles transition
    },

    expression: 'alert',
  };

  M.FSM._handlers.alert = alertHandler;
  console.log('[fsm/states/alert] v2 — jump + point (smooth transitions)');
})();
