// ═══════════════════════════════════════════════════════════
//  Mimic — FSM State: working  (eating animation)
//
//  File copy → character "eats": arms reach toward mouth,
//  mouth opens/closes rhythmically ("nom nom"), crumbs fly in.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const workingHandler = {
    enter() {
      M.eyeOpen = true;
      M.FSM._startWorkAnim();

      const A = M.Anim;
      A.mouthOpen = 0.6;
      A.leftArm = { dx: 2, dy: -3 };
      A.rightArm = { dx: -2, dy: -3 };
      A.bodySquash = 0;
      A.eatingProgress = 0;
      A.eatingNomCount = 0;
      A.eyePupilX = 0;
      A.eyePupilY = 1;

      M.Rendering.draw();
    },

    update(now) {
      const A = M.Anim;

      // Eyes sweep side-to-side
      const t = ((now % 600) / 600);
      M.eyeOffsetX = Math.sin(t * Math.PI * 2) * 8;

      // Excited bob
      A.bobOffset = Math.sin(now * 0.008) * 0.6;

      // ── Eating "nom" cycle (~400ms) ──────────────────
      const nomCycle = (now % 400) / 400;
      A.eatingNomCount = Math.floor(now / 400);

      if (nomCycle < 0.3) {
        A.mouthOpen = 0.5 + nomCycle / 0.3 * 0.5;
        A.bodySquash = -0.05;
        if (nomCycle < 0.1 && A.eatingNomCount % 3 === 0) {
          M.Rendering.spawnParticles();
          if (M.Audio) M.Audio.play('nom');
        }
      } else if (nomCycle < 0.5) {
        A.mouthOpen = 1 - (nomCycle - 0.3) / 0.2;
        A.bodySquash = 0.03;
      } else {
        A.mouthOpen = 0.3;
        A.bodySquash = 0;
      }

      const armBob = Math.sin(nomCycle * Math.PI * 2) * 0.5;
      A.leftArm.dy = -3 + armBob;
      A.rightArm.dy = -3 + armBob;

      A.eyePupilY = 1;
      A.headTilt = Math.sin(now * 0.003) * 0.2;
    },

    exit() {
      M.eyeOffsetX = 0;
      M.FSM._stopWorkAnim();

      const A = M.Anim;
      A.mouthOpen = 0;
      A.leftArm = { dx: 0, dy: 0 };
      A.rightArm = { dx: 0, dy: 0 };
      A.bodySquash = 0;
      A.eyePupilX = 0;
      A.eyePupilY = 0;
      A.headTilt = 0;
      A.eatingProgress = 0;
      // bobOffset NOT reset — smooth lerp handles transition
    },

    expression: 'open',
  };

  M.FSM._handlers.working = workingHandler;
  console.log('[fsm/states/working] v2 — eating nom cycle (smooth transitions)');
})();
