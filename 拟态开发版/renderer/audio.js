// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — Web Audio Synthesizer
//
//  Pure Web Audio API synthesis — no external audio files.
//  Sounds: chirp, yawn, boing, nom, bongo
//  Lazy-init AudioContext for browser autoplay policy.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let ctx = null;

  function getCtx() {
    if (!ctx) {
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) {
        console.warn('[audio] AudioContext not available:', e.message);
        return null;
      }
    }
    // Resume if suspended (autoplay policy)
    if (ctx.state === 'suspended') {
      ctx.resume().catch(() => {});
    }
    return ctx;
  }

  // ── Sound generators ──────────────────────────────────────

  function playChirp() {
    const ac = getCtx();
    if (!ac) return;
    const now = ac.currentTime;
    const dur = 0.15;

    const osc = ac.createOscillator();
    const gain = ac.createGain();

    osc.type = 'sine';
    osc.frequency.setValueAtTime(600, now);
    osc.frequency.linearRampToValueAtTime(1200, now + dur * 0.6);
    osc.frequency.linearRampToValueAtTime(900, now + dur);

    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.18, now + 0.01);
    gain.gain.linearRampToValueAtTime(0, now + dur);

    osc.connect(gain);
    gain.connect(ac.destination);
    osc.start(now);
    osc.stop(now + dur);
  }

  function playYawn() {
    const ac = getCtx();
    if (!ac) return;
    const now = ac.currentTime;
    const dur = 0.6;

    // Low tone
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(300, now);
    osc.frequency.linearRampToValueAtTime(200, now + dur);
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.12, now + 0.1);
    gain.gain.linearRampToValueAtTime(0, now + dur);
    osc.connect(gain);
    gain.connect(ac.destination);
    osc.start(now);
    osc.stop(now + dur);

    // Breath noise
    const bufferSize = ac.sampleRate * dur;
    const noiseBuffer = ac.createBuffer(1, bufferSize, ac.sampleRate);
    const data = noiseBuffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) {
      data[i] = (Math.random() * 2 - 1) * 0.06;
    }
    const noise = ac.createBufferSource();
    noise.buffer = noiseBuffer;
    const noiseGain = ac.createGain();
    noiseGain.gain.setValueAtTime(0.08, now);
    noiseGain.gain.linearRampToValueAtTime(0, now + dur);
    noise.connect(noiseGain);
    noiseGain.connect(ac.destination);
    noise.start(now);
    noise.stop(now + dur);
  }

  function playBoing() {
    const ac = getCtx();
    if (!ac) return;
    const now = ac.currentTime;
    const dur = 0.2;

    const osc = ac.createOscillator();
    const gain = ac.createGain();

    osc.type = 'sine';
    osc.frequency.setValueAtTime(400, now);
    osc.frequency.linearRampToValueAtTime(800, now + dur * 0.3);
    osc.frequency.linearRampToValueAtTime(400, now + dur);

    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.15, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);

    osc.connect(gain);
    gain.connect(ac.destination);
    osc.start(now);
    osc.stop(now + dur);
  }

  function playNom() {
    const ac = getCtx();
    if (!ac) return;
    const now = ac.currentTime;
    const dur = 0.05;

    const osc = ac.createOscillator();
    const gain = ac.createGain();

    osc.type = 'square';
    osc.frequency.setValueAtTime(200, now);
    osc.frequency.linearRampToValueAtTime(150, now + dur);

    gain.gain.setValueAtTime(0.06, now);
    gain.gain.linearRampToValueAtTime(0, now + dur);

    osc.connect(gain);
    gain.connect(ac.destination);
    osc.start(now);
    osc.stop(now + dur);
  }

  function playBongo() {
    const ac = getCtx();
    if (!ac) return;
    const now = ac.currentTime;
    const dur = 0.08;

    // Low percussive tap
    const osc = ac.createOscillator();
    const gain = ac.createGain();

    osc.type = 'sine';
    osc.frequency.setValueAtTime(180, now);
    osc.frequency.linearRampToValueAtTime(80, now + dur);

    gain.gain.setValueAtTime(0.2, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);

    osc.connect(gain);
    gain.connect(ac.destination);
    osc.start(now);
    osc.stop(now + dur);
  }

  // ── Public API ────────────────────────────────────────────

  const sounds = {
    chirp: playChirp,
    yawn: playYawn,
    boing: playBoing,
    nom: playNom,
    bongo: playBongo,
  };

  function play(name) {
    const fn = sounds[name];
    if (fn) {
      try { fn(); } catch (e) { /* silent degrade */ }
    }
  }

  M.Audio = { play, sounds, getCtx };
  console.log('[audio] Web Audio synthesizer ready — chirp|yawn|boing|nom|bongo');
})();
