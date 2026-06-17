// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — FSM State: idle
//  Default. Blink + breathing bob + eye tracking + yawn timer.
//  30s inactivity → yawn. 60s → sleepy. 120s → sleeping.
//  Mouse wake: sleepy/sleeping → stretch → idle.
//  Uses smooth bob lerp (no jumps).
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const BLINK_INTERVAL = 2200;
  const BLINK_CLOSED   = 150;
  const SLEEPY_TIME    = 60000;  // 60s → sleepy
  const SLEEPING_TIME  = 120000; // 120s → sleeping

  let yawnTimer = 0;
  let sleepPhase = 0;    // 0=awake, 1=sleepy, 2=sleeping
  let zzzTimer = 0;
  let wasAsleep = false; // track if we need wake-up stretch

  const idleHandler = {
    enter() {
      M.eyeOpen = true;
      M.lastBlink = performance.now();
      M.FSM._clearTimers();
      M.FSM._stopWorkAnim();

      const A = M.Anim;
      A.headTilt = 0;
      A.leftArm.dx = 0;  A.leftArm.dy = 0;
      A.rightArm.dx = 0; A.rightArm.dy = 0;
      A.leftLeg.dx = 0;  A.leftLeg.dy = 0;
      A.rightLeg.dx = 0; A.rightLeg.dy = 0;
      A.mouthOpen = 0;
      A.feetY = 0;
      A.yawnPhase = 0;
      A.targetScale = 1;

      // If we were sleeping, play wake-up stretch
      if (wasAsleep) {
        wasAsleep = false;
        this._doWakeStretch(A);
      }

      yawnTimer = performance.now();
      sleepPhase = 0;
      zzzTimer = 0;
      M._sleepPhase = 0;

      M.Rendering.draw();
    },

    update(now) {
      const A = M.Anim;
      const elapsed = now - M.FSM.stateStartTime;

      // ── Blink (only when not sleeping) ──────────────────
      if (sleepPhase < 2) {
        if (M.eyeOpen && now - M.lastBlink >= BLINK_INTERVAL) {
          M.eyeOpen = false;
          M.lastBlink = now;
        } else if (!M.eyeOpen && now - M.lastBlink >= BLINK_CLOSED) {
          M.eyeOpen = true;
          M.lastBlink = now;
        }
      } else {
        // Sleeping: eyes stay closed most of the time, rare slow blink
        if (!M.eyeOpen && now - M.lastBlink > 4000) {
          M.eyeOpen = true;
          M.lastBlink = now;
        } else if (M.eyeOpen && now - M.lastBlink > 300) {
          M.eyeOpen = false;
          M.lastBlink = now;
        }
      }

      // ── Breathing bob ───────────────────────────────────
      if (sleepPhase === 2) {
        // Sleeping: slower, deeper breathing
        A.bobOffset = Math.sin(elapsed * 0.0006) * 2.5;
      } else {
        A.bobOffset = Math.sin(elapsed * 0.0012) * 1.5;
      }

      // Head tilt
      if (sleepPhase === 2) {
        A.headTilt = -0.15 + Math.sin(elapsed * 0.0002) * 0.1;
      } else if (sleepPhase === 1) {
        A.headTilt = -0.1 + Math.sin(elapsed * 0.0003) * 0.15;
      } else {
        A.headTilt = Math.sin(elapsed * 0.0003) * 0.3;
      }

      // ── Idle time for sleep progression ─────────────────
      const idleTime = now - (M.lastActivity || now);

      // ── Sleep phase transitions ─────────────────────────
      if (idleTime > SLEEPING_TIME && sleepPhase < 2) {
        sleepPhase = 2;
        M._sleepPhase = 2;
        zzzTimer = now;
        // Sit pose
        A.leftLeg.dy = 2;
        A.rightLeg.dy = 2;
        A.feetY = 3;
        A.leftArm.dy = 1;
        A.rightArm.dy = 1;
        M.eyeOpen = false;
        if (M.Audio) M.Audio.play('yawn');
      } else if (idleTime > SLEEPY_TIME && sleepPhase < 1) {
        sleepPhase = 1;
        M._sleepPhase = 1;
        // Slightly droopy
        A.headTilt = -0.1;
        A.leftArm.dy = 0.5;
        A.rightArm.dy = 0.5;
      }

      // ── Zzz bubble (sleeping only) ──────────────────────
      if (sleepPhase === 2 && M.Bubble) {
        const sinceZzz = now - zzzTimer;
        if (sinceZzz > 4000 && Math.random() < 0.003) {
          M.Bubble.show('Zzz…', { duration: 2000, thought: true });
          zzzTimer = now;
        }
      }

      // ── Detect wake from sleep ──────────────────────────
      if (sleepPhase >= 1 && idleTime < 500) {
        // User moved mouse — waking up
        if (sleepPhase === 2) wasAsleep = true;
        sleepPhase = 0;
        M._sleepPhase = 0;
        M.eyeOpen = true;

        // Stand up
        A.leftLeg.dy = 0;
        A.rightLeg.dy = 0;
        A.feetY = 0;
        A.leftArm.dy = 0;
        A.rightArm.dy = 0;
        A.headTilt = 0;

        if (wasAsleep && M.Audio) M.Audio.play('yawn');
      }

      // ── Ambient raindrops (rare, when idle > 10s) ───────
      if (sleepPhase <= 1 && idleTime > 10000 && Math.random() < 0.0008 && M.Particles) {
        M.Particles.burst('rain', 1, { x: 2 + Math.random() * 12, y: -2 });
      }

      // ── Humming notes (very rare, awake idle > 15s) ──────
      if (sleepPhase === 0 && idleTime > 15000 && Math.random() < 0.0005 && M.Particles) {
        M.Particles.burst('note', 1, { x: 4 + Math.random() * 8, y: 0 });
      }

      // ── Yawn (only in awake phase) ──────────────────────
      if (sleepPhase === 0) {
        if (idleTime > 30000 && A.yawnPhase === 0) {
          A.yawnPhase = 1;
          yawnTimer = now;
          if (M.Audio) M.Audio.play('yawn');
        }

        if (A.yawnPhase >= 1) {
          const yt = Math.min(1, (now - yawnTimer) / 800);
          if (yt < 1) {
            A.yawnPhase = 1 + yt;
            A.mouthOpen = yt;
            A.leftArm.dy = -yt * 2;
            A.rightArm.dy = -yt * 2;
          } else {
            const holdTime = now - yawnTimer - 800;
            if (holdTime < 600) {
              A.yawnPhase = 2;
              A.mouthOpen = 1;
            } else {
              const rt = Math.min(1, (holdTime - 600) / 400);
              A.yawnPhase = 2 - rt;
              A.mouthOpen = 1 - rt;
              A.leftArm.dy = -(1 - rt) * 2;
              A.rightArm.dy = -(1 - rt) * 2;
              if (rt >= 1) {
                A.yawnPhase = 0;
                A.mouthOpen = 0;
                A.leftArm.dy = 0;
                A.rightArm.dy = 0;
                yawnTimer = now;
              }
            }
          }
        }
      }

      if (idleTime < 1000 && A.yawnPhase === 0) {
        yawnTimer = now;
      }
    },

    // ── Wake-up stretch animation ─────────────────────────
    _doWakeStretch(A) {
      A.bobOffset = -3;
      A.leftArm.dx = -2;
      A.leftArm.dy = -5;
      A.rightArm.dx = 2;
      A.rightArm.dy = -5;
      A.mouthOpen = 0.8;
      A.bodySquash = -0.08;

      setTimeout(() => {
        A.bobOffset = 0;
        A.leftArm.dx = 0;
        A.leftArm.dy = 0;
        A.rightArm.dx = 0;
        A.rightArm.dy = 0;
        A.mouthOpen = 0;
        A.bodySquash = 0;
      }, 800);
    },

    exit() {
      M.eyeOpen = true;
      M.Anim.yawnPhase = 0;
      M._sleepPhase = 0;
    },

    expression() {
      if (M.Anim.yawnPhase > 1) return 'surprised';
      if (sleepPhase >= 2) return 'sleeping';
      if (sleepPhase >= 1) return 'sleepy';
      if (!M.eyeOpen) return 'sleepy';
      return 'neutral';
    },
  };

  M.FSM._handlers.idle = idleHandler;
  console.log('[fsm/states/idle] v2 — smooth bob + yawn timer');
})();
