// ═══════════════════════════════════════════════════════════
//  Mimic v2.0 — Pixel Art Character with Articulated Limbs
//
//  16×20 grid. Separable head/torso/arms/legs.
//  Arm poses: rest | wave | reach | eat | point
//  Leg poses:  stand | sit | jump
//  Eye tracking with visible pupil shift, smooth bob lerp.
//  Mouth position exported as M.mouthCanvasPos for bubble tail.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const GW = 16, GH = 20;

  // ── Palette ──────────────────────────────────────────────
  const C = {
    skin:      '#FFB347',
    skinLight: '#FFD299',
    skinDark:  '#E8983E',
    eye:       '#2C2C2C',
    eyeWhite:  '#FFFFFF',
    eyeSpark:  '#FFD700',
    mouth:     '#2C2C2C',
    tongue:    '#FF6B6B',
    cheek:     '#FFAAAA',
    belly:     '#FFE0BD',
    hair:      '#E8983E',
    tooth:     '#FFFFFF',
    tongue2:   '#FF8888',
  };

  // ═══════════════════════════════════════════════════════════
  //  BODY PART DEFINITIONS  (grid coords [col, row])
  // ═══════════════════════════════════════════════════════════

  const PARTS = {
    head: [
      [6,1],[7,1],[8,1],[9,1],
      [4,2],[5,2],[6,2],[7,2],[8,2],[9,2],[10,2],[11,2],
      [4,3],[5,3],[6,3],[7,3],[8,3],[9,3],[10,3],[11,3],
      [4,4],[5,4],[6,4],[7,4],[8,4],[9,4],[10,4],[11,4],
      [4,5],[5,5],[6,5],[7,5],[8,5],[9,5],[10,5],[11,5],
      [5,6],[6,6],[7,6],[8,6],[9,6],[10,6],
    ],
    hair: [[7,0],[8,0],[6,1],[7,1],[8,1],[9,1]],
    hairShadow: [[7,0]],
    neck: [[7,7],[8,7]],

    torso: [5,6,7,8,9,10].flatMap(c => [8,9,10,11,12].map(r => [c,r])),
    belly: [6,7,8,9].flatMap(c => [9,10,11].map(r => [c,r])),

    leftUpperArm:  [3,4].flatMap(c => [8,9,10].map(r => [c,r])),
    leftLowerArm:  [3,4].flatMap(c => [11,12].map(r => [c,r])),
    leftHand:      [[3,13],[4,13]],

    rightUpperArm: [11,12].flatMap(c => [8,9,10].map(r => [c,r])),
    rightLowerArm: [11,12].flatMap(c => [11,12].map(r => [c,r])),
    rightHand:     [[11,13],[12,13]],

    leftUpperLeg:  [5,6].flatMap(c => [14,15,16].map(r => [c,r])),
    leftLowerLeg:  [5,6].flatMap(c => [17,18].map(r => [c,r])),
    leftFoot:      [4,5,6].flatMap(c => [19].map(r => [c,r])),

    rightUpperLeg: [9,10].flatMap(c => [14,15,16].map(r => [c,r])),
    rightLowerLeg: [9,10].flatMap(c => [17,18].map(r => [c,r])),
    rightFoot:     [9,10,11].flatMap(c => [19].map(r => [c,r])),
  };

  // Mouth centre (grid coords) — for bubble tail attachment
  const MOUTH_GRID = { col: 7.5, row: 5 };

  // ═══════════════════════════════════════════════════════════
  //  ANIMATION STATE  (modified by FSM handlers)
  // ═══════════════════════════════════════════════════════════

  if (!M.Anim) {
    M.Anim = {
      bobOffset: 0,
      _smoothBob: 0,         // lerped bob (prevents position jumps)
      headTilt: 0,
      leftArm:  { dx: 0, dy: 0 },
      rightArm: { dx: 0, dy: 0 },
      leftLeg:  { dx: 0, dy: 0 },
      rightLeg: { dx: 0, dy: 0 },
      feetY: 0,
      mouthOpen: 0,
      eyePupilX: 0,
      eyePupilY: 0,
      eatingProgress: 0,
      eatingNomCount: 0,
      bodySquash: 0,
      bodyScale: 1,
      targetScale: 1,
      yawnPhase: 0,
      showHeadphones: false,
    };
  }

  // ═══════════════════════════════════════════════════════════
  //  DRAWING HELPERS
  // ═══════════════════════════════════════════════════════════

  function drawCell(ctx, col, row, color, S, gx, gy) {
    ctx.fillStyle = color;
    ctx.fillRect(
      Math.floor(gx + col * S),
      Math.floor(gy + row * S),
      Math.ceil(S),
      Math.ceil(S)
    );
  }

  function fillCells(ctx, cells, offsetX, offsetY, color, S, gx, gy) {
    ctx.fillStyle = color;
    const s = Math.ceil(S);
    for (const [c, r] of cells) {
      ctx.fillRect(
        Math.floor(gx + (c + offsetX) * S),
        Math.floor(gy + (r + offsetY) * S),
        s, s
      );
    }
  }

  // ═══════════════════════════════════════════════════════════
  //  DRAW BODY
  // ═══════════════════════════════════════════════════════════

  function drawBody(ctx, S, gx, gy) {
    const A = M.Anim;
    const P = PARTS;

    // Legs
    fillCells(ctx, P.leftUpperLeg,  A.leftLeg.dx,  A.leftLeg.dy,  C.skin, S, gx, gy);
    fillCells(ctx, P.rightUpperLeg, A.rightLeg.dx, A.rightLeg.dy, C.skin, S, gx, gy);
    fillCells(ctx, P.leftLowerLeg,  A.leftLeg.dx,  A.leftLeg.dy,  C.skin, S, gx, gy);
    fillCells(ctx, P.rightLowerLeg, A.rightLeg.dx, A.rightLeg.dy, C.skin, S, gx, gy);

    const footOff = A.feetY || 0;
    for (const [c, r] of P.leftFoot)  drawCell(ctx, c, r + footOff, C.skinDark, S, gx, gy);
    for (const [c, r] of P.rightFoot) drawCell(ctx, c, r + footOff, C.skinDark, S, gx, gy);

    // Torso
    fillCells(ctx, P.torso, 0, 0, C.skin, S, gx, gy);
    fillCells(ctx, P.belly, 0, 0, C.belly, S, gx, gy);

    // Left arm (behind torso)
    fillCells(ctx, P.leftUpperArm, A.leftArm.dx, A.leftArm.dy, C.skin, S, gx, gy);
    fillCells(ctx, P.leftLowerArm, A.leftArm.dx, A.leftArm.dy, C.skin, S, gx, gy);
    fillCells(ctx, P.leftHand,     A.leftArm.dx, A.leftArm.dy, C.skinLight, S, gx, gy);

    // Neck
    fillCells(ctx, P.neck, 0, 0, C.skinLight, S, gx, gy);

    // Head
    const tilt = A.headTilt || 0;
    fillCells(ctx, P.head, tilt * 0.5, 0, C.skin, S, gx, gy);

    // Hair
    fillCells(ctx, P.hair, tilt * 0.5, 0, C.hair, S, gx, gy);
    fillCells(ctx, P.hairShadow, tilt * 0.5, 0, C.skinDark, S, gx, gy);

    // Right arm (in front)
    fillCells(ctx, P.rightUpperArm, A.rightArm.dx, A.rightArm.dy, C.skin, S, gx, gy);
    fillCells(ctx, P.rightLowerArm, A.rightArm.dx, A.rightArm.dy, C.skin, S, gx, gy);
    fillCells(ctx, P.rightHand,     A.rightArm.dx, A.rightArm.dy, C.skinLight, S, gx, gy);
  }

  // ═══════════════════════════════════════════════════════════
  //  DRAW FACE
  //  Eye tracking: dark 2×2 blocks shift by pupil offset
  //  (the block moves as a unit, clearly visible)
  // ═══════════════════════════════════════════════════════════

  function drawFace(ctx, expr, S, gx, gy) {
    const A = M.Anim;
    const eyeOpen = M.eyeOpen !== false;

    // ── Pupil tracking offset (visible: shifts entire eye block) ──
    const px = Math.round(Math.max(-2, Math.min(2, A.eyePupilX || 0)));
    const py = Math.round(Math.max(-1, Math.min(1, A.eyePupilY || 0)));

    // ── Cheeks ─────────────────────────────────────────
    if (expr.cheek) {
      drawCell(ctx, 4, 3, C.cheek, S, gx, gy);
      drawCell(ctx, 5, 3, C.cheek, S, gx, gy);
      drawCell(ctx, 10, 3, C.cheek, S, gx, gy);
      drawCell(ctx, 11, 3, C.cheek, S, gx, gy);
    }

    // ── Eyes ───────────────────────────────────────────
    const eyeStyle = expr.eyes;

    // Base positions (shifted by pupil tracking)
    const lx = 5 + px;
    const rx = 9 + px;
    const ey = 2 + py;

    function e(c, r, color) { drawCell(ctx, c, r, color || C.eye, S, gx, gy); }
    function w(c, r)        { drawCell(ctx, c, r, C.eyeWhite, S, gx, gy); }
    function sp(c, r)       { drawCell(ctx, c, r, C.eyeSpark, S, gx, gy); }

    switch (eyeStyle) {

      case 'round':
        if (eyeOpen) {
          // ● ●  2×2 blocks at tracked position
          e(lx, ey); e(lx+1, ey); e(lx, ey+1); e(lx+1, ey+1);
          e(rx, ey); e(rx+1, ey); e(rx, ey+1); e(rx+1, ey+1);
          // White highlight dot at upper-left (moves with eye)
          w(lx, ey); w(rx, ey);
        }
        break;

      case 'arch':
        // ^ ^  top row only
        e(lx, ey); e(lx+1, ey);
        e(rx, ey); e(rx+1, ey);
        break;

      case 'wide':
        // ○ ○  3×3 larger
        for (let c = lx-1; c <= lx+1; c++) for (let r = ey-1; r <= ey+1; r++) e(c, r);
        for (let c = rx-1; c <= rx+1; c++) for (let r = ey-1; r <= ey+1; r++) e(c, r);
        w(lx, ey); w(rx, ey);
        break;

      case 'closed':
        // — —  3×1 flat
        e(lx-1, ey+1); e(lx, ey+1); e(lx+1, ey+1);
        e(rx-1, ey+1); e(rx, ey+1); e(rx+1, ey+1);
        break;

      case 'closed_arch':
        // > <
        e(lx-1, ey); e(lx, ey+1); e(lx+1, ey);
        e(rx-1, ey); e(rx, ey+1); e(rx+1, ey);
        break;

      case 'half_closed':
        // = =  half-lidded (sleepy)
        e(lx, ey+1); e(lx+1, ey+1);
        e(lx, ey); e(lx+1, ey);
        e(rx, ey+1); e(rx+1, ey+1);
        e(rx, ey); e(rx+1, ey);
        break;

      case 'sparkle':
        // ✦ ✦  golden cross-star
        [lx+1, rx].forEach(cx => {
          sp(cx, ey+1);
          sp(cx-1, ey+1); sp(cx+1, ey+1);
          sp(cx, ey); sp(cx, ey+2);
        });
        break;

      default:
        e(lx, ey); e(lx+1, ey); e(lx, ey+1); e(lx+1, ey+1);
        e(rx, ey); e(rx+1, ey); e(rx, ey+1); e(rx+1, ey+1);
    }

    // Closed blink for 'round'
    if (eyeStyle === 'round' && !eyeOpen) {
      e(lx-1, ey+1); e(lx, ey+1); e(lx+1, ey+1);
      e(rx-1, ey+1); e(rx, ey+1); e(rx+1, ey+1);
    }

    // ── Mouth ──────────────────────────────────────────
    const mo = A.mouthOpen || 0;

    switch (expr.mouth) {

      case 'smile':
        if (mo > 0.5) {
          e(6, 4); e(7, 4); e(8, 4); e(9, 4);
          e(6, 5); e(9, 5);
          e(7, 5, C.tongue); e(8, 5, C.tongue);
        } else {
          e(6, 5); e(7, 4); e(8, 4); e(9, 5);
        }
        break;

      case 'open':
        if (mo > 0) {
          const h = mo > 0.7 ? 3 : 2;
          e(6, 4); e(7, 4); e(8, 4); e(9, 4);
          e(6, 5); e(7, 5); e(8, 5); e(9, 5);
          if (h === 3) { e(6, 6); e(7, 6); e(8, 6); e(9, 6); }
          e(7, 5, C.tongue); e(8, 5, C.tongue);
        } else {
          e(6, 4); e(7, 4); e(8, 4); e(9, 4);
          e(6, 5); e(7, 5); e(8, 5); e(9, 5);
          e(7, 5, C.tongue); e(8, 5, C.tongue);
        }
        break;

      case 'grin':
        e(5, 4); e(6, 4); e(7, 4); e(8, 4); e(9, 4); e(10, 4);
        e(5, 5); e(6, 5); e(7, 5); e(8, 5); e(9, 5); e(10, 5);
        e(6, 4, C.tooth); e(7, 4, C.tooth); e(8, 4, C.tooth); e(9, 4, C.tooth);
        break;

      case 'none':
      default:
        break;
    }

    // ── Yawn override ──────────────────────────────────
    if (A.yawnPhase > 0) {
      const yp = A.yawnPhase;
      e(5, 4); e(6, 4); e(7, 4); e(8, 4); e(9, 4); e(10, 4);
      e(5, 5); e(6, 5); e(7, 5); e(8, 5); e(9, 5); e(10, 5);
      if (yp > 1.5) {
        e(5, 6); e(6, 6); e(7, 6); e(8, 6); e(9, 6); e(10, 6);
      }
      e(7, 5, C.tongue2); e(8, 5, C.tongue2);
      // Eyes forced closed
      e(4, 3); e(5, 3); e(6, 3);
      e(8, 3); e(9, 3); e(10, 3);
    }
  }

  // ═══════════════════════════════════════════════════════════
  //  HEADPHONES  (listening state accessory)
  // ═══════════════════════════════════════════════════════════

  function drawHeadphones(ctx, S, gx, gy) {
    const A = M.Anim;
    if (!A.showHeadphones) return;

    const tilt = A.headTilt || 0;
    const h = Math.ceil(S);
    const tx = Math.round(gx + tilt * 0.5 * S);

    // Headphone color
    const bandColor = '#555';
    const cupColor  = '#444';
    const padColor  = '#666';

    // ── Band over head (cols 5-10, row 0-1) ──────────────
    ctx.fillStyle = bandColor;
    for (let c = 5; c <= 10; c++) {
      ctx.fillRect(Math.floor(tx + c * S), Math.floor(gy + 0 * S), h, h);
    }
    // band sides going down to ear cups
    ctx.fillRect(Math.floor(tx + 4 * S), Math.floor(gy + 1 * S), h, h * 2);
    ctx.fillRect(Math.floor(tx + 11 * S), Math.floor(gy + 1 * S), h, h * 2);

    // ── Left ear cup (cols 3-4, rows 2-3) ──────────────
    ctx.fillStyle = cupColor;
    ctx.fillRect(Math.floor(tx + 3 * S), Math.floor(gy + 2 * S), h * 2, h * 2);
    ctx.fillStyle = padColor;
    ctx.fillRect(Math.floor(tx + 3 * S), Math.floor(gy + 3 * S), h * 2, h);

    // ── Right ear cup (cols 11-12, rows 2-3) ────────────
    ctx.fillStyle = cupColor;
    ctx.fillRect(Math.floor(tx + 11 * S), Math.floor(gy + 2 * S), h * 2, h * 2);
    ctx.fillStyle = padColor;
    ctx.fillRect(Math.floor(tx + 11 * S), Math.floor(gy + 3 * S), h * 2, h);
  }

  // ═══════════════════════════════════════════════════════════
  //  EATING PARTICLES
  // ═══════════════════════════════════════════════════════════

  let particles = [];

  function spawnEatParticles() {
    for (let i = 0; i < 3; i++) {
      particles.push({
        x: -2 + Math.random() * 20,
        y: 8 + Math.random() * 5,
        tx: 7.5, ty: 5,
        life: 1,
        decay: 0.03 + Math.random() * 0.04,
      });
    }
  }

  function updateParticles(ctx, S, gx, gy) {
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.life -= p.decay;
      p.x += (p.tx - p.x) * 0.15;
      p.y += (p.ty - p.y) * 0.15;
      if (p.life <= 0) { particles.splice(i, 1); continue; }
      drawCell(ctx, Math.round(p.x), Math.round(p.y),
               p.life > 0.5 ? C.skinLight : C.skinDark, S, gx, gy);
    }
  }

  // ═══════════════════════════════════════════════════════════
  //  MAIN DRAW  (with smooth bob lerp to prevent position jumps)
  // ═══════════════════════════════════════════════════════════

  function drawPixelCharacter(ctx, centerX, centerY) {
    const petSize = M.petSize;
    const A = M.Anim;

    // Smooth scale
    A.bodyScale += ((A.targetScale || 1) - A.bodyScale) * 0.15;
    const scale = A.bodyScale;
    const effectiveSize = petSize * scale;

    const cellSize = Math.max(2, Math.round(effectiveSize / GH));
    const totalW = GW * cellSize;
    const totalH = GH * cellSize;

    // ── Smooth bob lerp (prevents position jumps on state change) ──
    if (A._smoothBob === undefined) A._smoothBob = 0;
    A._smoothBob += (A.bobOffset - A._smoothBob) * 0.22;
    const bob = A._smoothBob;

    const gx = Math.floor(centerX - totalW / 2);
    const gy = Math.floor(centerY - totalH / 2 + bob);

    ctx.imageSmoothingEnabled = false;

    const expr = M.FSM ? M.FSM.getExpression() : M.EXPRESSIONS.neutral;

    drawBody(ctx, cellSize, gx, gy);
    drawFace(ctx, expr, cellSize, gx, gy);
    drawHeadphones(ctx, cellSize, gx, gy);  // 🎧 listening state
    // Unified particles (v2.1): legacy eat particles + new effects
    if (M.Particles && M.Particles.count() > 0) {
      M.Particles.updateAndDraw(ctx, cellSize, gx, gy);
    } else if (particles.length > 0) {
      updateParticles(ctx, cellSize, gx, gy);
    }

    // ── Export mouth position (canvas px) for bubble attachment ──
    const tilt = A.headTilt || 0;
    M.mouthCanvasPos = {
      x: gx + (MOUTH_GRID.col + tilt * 0.5) * cellSize,
      y: gy + MOUTH_GRID.row * cellSize,
    };
  }

  function draw() {
    const canvas = M.canvas, ctx = M.ctx;
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, M.winW, M.winH);
    ctx.imageSmoothingEnabled = false;
    drawPixelCharacter(ctx, M.petCX, M.petCY);
  }

  function burstEatParticles(count) {
    for (let i = 0; i < count; i++) {
      particles.push({
        x: -2 + Math.random() * 20,
        y: 4 + Math.random() * 8,
        tx: 7.5, ty: 5,
        life: 1,
        decay: 0.02 + Math.random() * 0.05,
      });
    }
  }

  M.Rendering = M.Rendering || {};
  M.Rendering.drawCharacter = drawPixelCharacter;
  M.Rendering.draw = draw;
  M.Rendering.spawnEatParticles = burstEatParticles;
  M.Rendering.spawnParticles = spawnEatParticles;

  console.log('[pixel-character v2] eye tracking (block shift), smooth bob lerp active');
})();
