// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — Particle Effects Engine
//
//  Unified particle system for all visual effects.
//  Types: heart (❤), star (✦), spark (·), eat (crumb)
//  Physics: float upward + horizontal drift + fade out.
//  Rendered as pixel-art cells on the character canvas.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  // Particle pool
  let particles = [];

  // ── Particle type definitions ──────────────────────────────

  const TYPES = {
    heart: {
      color: '#FF6B8A',
      colorAlt: '#FF8FAF',
      size: 1,
      lifeMin: 0.8,
      lifeMax: 1.4,
      vyMin: -1.2,
      vyMax: -2.5,
      vxRange: 0.8,
      shape: 'heart',
    },
    star: {
      color: '#FFD700',
      colorAlt: '#FFEA70',
      size: 1,
      lifeMin: 1.0,
      lifeMax: 1.8,
      vyMin: -0.8,
      vyMax: -2.0,
      vxRange: 0.6,
      shape: 'star',
    },
    spark: {
      color: '#FFFFFF',
      colorAlt: '#FFEECC',
      size: 0.6,
      lifeMin: 0.4,
      lifeMax: 0.9,
      vyMin: -1.5,
      vyMax: -1.0,
      vxRange: 1.5,
      shape: 'dot',
    },
    eat: {
      color: '#FFD299',
      colorAlt: '#E8983E',
      size: 0.7,
      lifeMin: 0.5,
      lifeMax: 1.0,
      vyMin: -0.3,
      vyMax: -0.8,
      vxRange: 0.4,
      shape: 'dot',
      target: true,
    },
    note: {
      color: '#8B5CF6',
      colorAlt: '#A78BFA',
      size: 1.1,
      lifeMin: 1.0,
      lifeMax: 2.0,
      vyMin: -0.8,
      vyMax: -1.8,
      vxRange: 1.0,
      shape: 'note',
    },
    rain: {
      color: '#60A5FA',
      colorAlt: '#93C5FD',
      size: 0.5,
      lifeMin: 1.5,
      lifeMax: 3.0,
      vyMin: -0.2,
      vyMax: -0.5,
      vxRange: 0.2,
      shape: 'rain',
    },
  };

  // ── Spawn ──────────────────────────────────────────────────

  function burst(type, count, origin) {
    const def = TYPES[type];
    if (!def) return;

    const ox = origin ? origin.x : 7.5;
    const oy = origin ? origin.y : 4;

    for (let i = 0; i < count; i++) {
      const life = def.lifeMin + Math.random() * (def.lifeMax - def.lifeMin);
      particles.push({
        type,
        x: ox + (Math.random() - 0.5) * 4,
        y: oy + (Math.random() - 0.5) * 2,
        vx: (Math.random() - 0.5) * def.vxRange * 2,
        vy: -(def.vyMin + Math.random() * (def.vyMax - def.vyMin)),
        life,
        maxLife: life,
        color: Math.random() > 0.5 ? def.color : def.colorAlt,
        size: def.size,
        shape: def.shape,
        target: def.target || false,
        tx: ox,
        ty: oy - 2,
      });
    }
  }

  // Eat particles — fly toward mouth target
  function spawnEatParticles(count) {
    const def = TYPES.eat;
    const ox = -2;
    const oy = 4 + Math.random() * 5;
    const tx = 7.5;
    const ty = 5;

    for (let i = 0; i < (count || 3); i++) {
      const life = def.lifeMin + Math.random() * (def.lifeMax - def.lifeMin);
      particles.push({
        type: 'eat',
        x: ox + Math.random() * 20,
        y: oy + Math.random() * 8,
        vx: (Math.random() - 0.5) * def.vxRange * 2,
        vy: -(def.vyMin + Math.random() * (def.vyMax - def.vyMin)),
        life,
        maxLife: life,
        color: Math.random() > 0.5 ? def.color : def.colorAlt,
        size: def.size,
        shape: 'dot',
        target: true,
        tx,
        ty,
      });
    }
  }

  // ── Update & Draw ──────────────────────────────────────────

  function updateAndDraw(ctx, cellSize, gx, gy) {
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];

      // Update
      p.life -= 0.016; // ~60fps delta
      if (p.life <= 0) {
        particles.splice(i, 1);
        continue;
      }

      if (p.target) {
        // Fly toward target (mouth)
        p.x += (p.tx - p.x) * 0.15;
        p.y += (p.ty - p.y) * 0.15;
      } else {
        // Float upward + drift
        p.x += p.vx * 0.05;
        p.y += p.vy * 0.05;
        p.vy += 0.02; // slight gravity
        p.vx *= 0.98;
      }

      // Alpha from life ratio
      const alpha = Math.max(0, p.life / p.maxLife);
      ctx.globalAlpha = alpha;

      // Draw
      const px = Math.floor(gx + p.x * cellSize);
      const py = Math.floor(gy + p.y * cellSize);
      const s = Math.ceil(cellSize * p.size);

      switch (p.shape) {
        case 'heart':
          drawHeart(ctx, px, py, s, p.color);
          break;
        case 'star':
          drawStar(ctx, px, py, s, p.color);
          break;
        case 'note':
          drawMusicNote(ctx, px, py, s, p.color);
          break;
        case 'rain':
          drawRaindrop(ctx, px, py, s, p.color);
          break;
        case 'dot':
        default:
          ctx.fillStyle = p.color;
          ctx.fillRect(px, py, s, s);
          break;
      }

      ctx.globalAlpha = 1;
    }
  }

  function drawHeart(ctx, x, y, s, color) {
    ctx.fillStyle = color;
    // 2×2 heart shape:  █ █
    //                    ███
    //                     █
    const h = Math.ceil(s);
    ctx.fillRect(x, y, h, h);
    ctx.fillRect(x + h * 2, y, h, h);
    ctx.fillRect(x - h, y + h, h * 4, h);
    ctx.fillRect(x, y + h * 2, h, h);
    ctx.fillRect(x + h, y + h * 2, h, h);
  }

  function drawStar(ctx, x, y, s, color) {
    ctx.fillStyle = color;
    const h = Math.ceil(s);
    // 3×3 star cross shape
    ctx.fillRect(x + h, y, h, h);           // top
    ctx.fillRect(x, y + h, h * 3, h);       // middle row
    ctx.fillRect(x + h, y + h * 2, h, h);   // bottom
  }

  function drawMusicNote(ctx, x, y, s, color) {
    ctx.fillStyle = color;
    const h = Math.ceil(s);
    // ♪ pixel art: vertical stem + note head + flag
    //   ██
    //   ██
    //   ██  ██
    //   ██████
    //    ████
    ctx.fillRect(x + h, y, h * 2, h);       // top stem
    ctx.fillRect(x + h, y + h, h * 2, h);   // mid stem
    ctx.fillRect(x + h, y + h * 2, h * 4, h); // stem + head row
    ctx.fillRect(x + h * 2, y + h * 3, h * 2, h); // head bottom
    ctx.fillRect(x, y + h, h, h * 2);       // flag left
  }

  function drawRaindrop(ctx, x, y, s, color) {
    ctx.fillStyle = color;
    const h = Math.ceil(s);
    // 💧 pixel art: teardrop shape
    //     ██
    //    ████
    //    ████
    //     ██
    ctx.fillRect(x + h, y, h * 2, h);       // top point
    ctx.fillRect(x, y + h, h * 4, h);       // mid wide
    ctx.fillRect(x + h, y + h * 2, h * 2, h); // bottom narrow
  }

  // ── Clear ──────────────────────────────────────────────────

  function clear() {
    particles.length = 0;
  }

  function count() {
    return particles.length;
  }

  // ── Export ─────────────────────────────────────────────────

  // Backward compat: hook into existing Rendering.spawnEatParticles
  const oldSpawn = M.Rendering && M.Rendering.spawnEatParticles;
  M.Rendering = M.Rendering || {};
  M.Rendering.spawnEatParticles = spawnEatParticles;
  M.Rendering.spawnParticles = spawnEatParticles;

  M.Particles = {
    burst,
    spawnEatParticles,
    updateAndDraw,
    clear,
    count,
  };

  console.log('[particles] engine ready — heart|star|spark|eat|note|rain');
})();
