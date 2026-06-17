// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — Window Position Store
//
//  Persists window position to config/position.json so the
//  pet reappears where the user last left it.
// ═══════════════════════════════════════════════════════════

const fs = require('fs');
const path = require('path');

const POSITION_FILE = path.join(__dirname, '..', 'config', 'position.json');

function loadPosition() {
  try {
    if (fs.existsSync(POSITION_FILE)) {
      const data = JSON.parse(fs.readFileSync(POSITION_FILE, 'utf-8'));
      if (typeof data.x === 'number' && typeof data.y === 'number') {
        console.log('[position-store] loaded:', data);
        return data;
      }
    }
  } catch (err) {
    console.warn('[position-store] load error:', err.message);
  }
  console.log('[position-store] no saved position, using default');
  return null;
}

function savePosition(x, y) {
  try {
    const dir = path.dirname(POSITION_FILE);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    const data = { x: Math.round(x), y: Math.round(y) };
    fs.writeFileSync(POSITION_FILE, JSON.stringify(data, null, 2), 'utf-8');
    console.log('[position-store] saved:', data);
  } catch (err) {
    console.warn('[position-store] save error:', err.message);
  }
}

module.exports = { loadPosition, savePosition };
