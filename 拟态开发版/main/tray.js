// ═══════════════════════════════════════════════════════════
//  Mimic — System Tray Manager
//
//  Orange pet icon tray with context menu:
//    - Show/Hide window
//    - Size switching (large/medium/small)
//    - Exit
//  Double-click toggles window visibility.
// ═══════════════════════════════════════════════════════════

const { Tray, Menu, nativeImage } = require('electron');
const { getConfig } = require('./config');
const { quitApp, setPassthrough, isPassthrough } = require('./window');

let tray = null;
let lastKnownPetSize = getConfig().defaultSize || 80;

// ── Programmatic tray icon (16×16 orange circle + eyes) ──

function createTrayIcon() {
  const size = 16;
  const buf = Buffer.alloc(size * size * 4);

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i  = (y * size + x) * 4;
      const bodyR = 7, bodyCX = 8, bodyCY = 8;
      const inBody = Math.hypot(x - bodyCX, y - bodyCY) <= bodyR;
      const inEye = (Math.hypot(x - 5, y - 6) <= 1.5) || (Math.hypot(x - 11, y - 6) <= 1.5);

      if (inBody && !inEye) {
        buf[i]     = 0;    // R — BGRA order
        buf[i + 1] = 165;  // G
        buf[i + 2] = 255;  // B
        buf[i + 3] = 255;  // A
      } else if (inEye) {
        buf[i]     = 0;
        buf[i + 1] = 0;
        buf[i + 2] = 0;
        buf[i + 3] = 255;
      } else {
        buf[i + 3] = 0;    // transparent
      }
    }
  }

  return nativeImage.createFromBitmap(buf, { width: size, height: size });
}

// ── Build context menu ────────────────────────────────────

function buildTrayMenu(win) {
  const makeSizeItem = (label, petSize) => ({
    label,
    type: 'radio',
    checked: lastKnownPetSize === petSize,
    click: () => {
      if (!win) return;
      lastKnownPetSize = petSize;
      win.webContents.send('size-changed', petSize);
    },
  });

  const passthroughOn = isPassthrough();

  return Menu.buildFromTemplate([
    { label: '显示/隐藏', click: () => {
      if (!win) return;
      win.isVisible() ? win.hide() : win.show();
    }},
    { type: 'separator' },
    makeSizeItem('大 (Large)',  110),
    makeSizeItem('中 (Medium)', 80),
    makeSizeItem('小 (Small)',  50),
    { type: 'separator' },
    { label: '穿透模式 (Click-through)', type: 'checkbox',
      checked: passthroughOn,
      click: () => {
        setPassthrough(!passthroughOn);
        // Refresh this menu to reflect new state
        if (tray) tray.setContextMenu(buildTrayMenu(win));
      }},
    { label: '退出', click: () => {
      tray.destroy();
      quitApp();
    }},
  ]);
}

// ── Create tray ───────────────────────────────────────────

function createTray(win) {
  tray = new Tray(createTrayIcon());
  tray.setToolTip('拟态 桌宠');
  tray.setContextMenu(buildTrayMenu(win));

  tray.on('double-click', () => {
    if (!win) return;
    win.isVisible() ? win.hide() : win.show();
  });

  console.log('[main/tray] created');
  return tray;
}

function getTray() {
  return tray;
}

function getLastKnownPetSize() {
  return lastKnownPetSize;
}

function setLastKnownPetSize(size) {
  lastKnownPetSize = size;
}

function refreshTrayMenu(win) {
  if (tray) {
    tray.setContextMenu(buildTrayMenu(win));
  }
}

module.exports = {
  createTray,
  getTray,
  getLastKnownPetSize,
  setLastKnownPetSize,
  refreshTrayMenu,
};
