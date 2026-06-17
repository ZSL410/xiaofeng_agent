// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — IPC Handlers
//
//  Registers all IPC channels between renderer and main:
//    - window-drag        — delta-based window repositioning
//    - resize-window      — smooth animated resize
//    - set-pet-size       — track pet size for tray menu
//    - show-context-menu  — right-click size switching
//    - get-config         — send config to renderer
//    - save-position      — persist window position
// ═══════════════════════════════════════════════════════════

const { ipcMain, Menu } = require('electron');
const { getWindow, quitApp, setPassthrough, isPassthrough } = require('./window');
const { setLastKnownPetSize, refreshTrayMenu } = require('./tray');
const { getConfig } = require('./config');
const { savePosition } = require('./position-store');

let resizeAnimId = null;

function registerIpcHandlers() {

  // ── Window drag (ultra-defensive, handles all arg formats) ──
  ipcMain.on('window-drag', (_e, a, b) => {
    const win = getWindow();
    if (!win) return;

    // Unpack dx/dy from whatever format the renderer sent
    let ddx = 0, ddy = 0;
    if (typeof a === 'number' && typeof b === 'number') {
      // Flat args: dx, dy
      ddx = Math.round(a);
      ddy = Math.round(b);
    } else if (typeof a === 'number') {
      ddx = Math.round(a);
      ddy = 0;
    } else if (a && typeof a === 'object' && !Array.isArray(a)) {
      // Object: { dx, dy }
      ddx = Math.round(Number(a.dx));
      ddy = Math.round(Number(a.dy));
    } else if (Array.isArray(a)) {
      // Array: [dx, dy]
      ddx = Math.round(Number(a[0]));
      ddy = Math.round(Number(a[1]));
    }

    if (isNaN(ddx)) ddx = 0;
    if (isNaN(ddy)) ddy = 0;
    if (ddx === 0 && ddy === 0) return;

    try {
      const [x, y] = win.getPosition();
      win.setPosition(x + ddx, y + ddy);
    } catch (err) {
      console.error('[main/ipc] window-drag setPosition error:', err.message);
    }
  });

  // ── Dynamic window resize (ultra-defensive, animated ease-out) ──
  ipcMain.on('resize-window', (_e, a, b) => {
    const win = getWindow();
    if (!win) return;

    // Unpack w/h from whatever format
    let targetW = 0, targetH = 0;
    if (typeof a === 'number' && typeof b === 'number') {
      targetW = Math.round(a);
      targetH = Math.round(b);
    } else if (typeof a === 'number') {
      targetW = Math.round(a);
    } else if (a && typeof a === 'object' && !Array.isArray(a)) {
      targetW = Math.round(Number(a.w));
      targetH = Math.round(Number(a.h));
    } else if (Array.isArray(a)) {
      targetW = Math.round(Number(a[0]));
      targetH = Math.round(Number(a[1]));
    }

    if (isNaN(targetW) || isNaN(targetH) || targetW <= 0 || targetH <= 0) return;

    if (resizeAnimId) {
      clearInterval(resizeAnimId);
      resizeAnimId = null;
    }

    try {
      const [curW, curH] = win.getSize();

      // Close enough → snap directly
      if (Math.abs(curW - targetW) < 4 && Math.abs(curH - targetH) < 4) {
        win.setSize(targetW, targetH);
        return;
      }

      // Smooth animate (ease-out cubic over ~8 steps, ~60fps)
      const STEPS = 8;
      const INTERVAL = 16;
      let step = 0;
      const startW = curW, startH = curH;

      resizeAnimId = setInterval(() => {
        step++;
        const t = step / STEPS;
        const ease = 1 - Math.pow(1 - t, 3);
        const newW = Math.round(startW + (targetW - startW) * ease);
        const newH = Math.round(startH + (targetH - startH) * ease);

        try {
          win.setSize(newW, newH);
        } catch (err) {
          console.error('[main/ipc] resize setSize error:', err.message);
          clearInterval(resizeAnimId);
          resizeAnimId = null;
        }

        if (step >= STEPS) {
          clearInterval(resizeAnimId);
          resizeAnimId = null;
          try { win.setSize(targetW, targetH); } catch (e) {}
        }
      }, INTERVAL);
    } catch (err) {
      console.error('[main/ipc] resize-window error:', err.message);
    }
  });

  // ── Pet size tracking (for tray menu checked state, type-safe) ──
  ipcMain.on('set-pet-size', (_e, petSize) => {
    const size = Number(petSize);
    if (isNaN(size) || size <= 0) return;
    setLastKnownPetSize(size);
    refreshTrayMenu(getWindow());
  });

  // ── Right-click context menu (size switching) ────────────
  ipcMain.on('show-context-menu', (event, currentPetSize, appVersion) => {
    const makeItem = (label, petSize) => ({
      label,
      type: 'radio',
      checked: currentPetSize === petSize,
      click: () => {
        setLastKnownPetSize(petSize);
        event.sender.send('size-changed', petSize);
      },
    });

    const passthroughOn = isPassthrough();

    const menu = Menu.buildFromTemplate([
      { label: '拟态 v' + (appVersion || '?.?'), enabled: false },
      { type: 'separator' },
      makeItem('大 (Large)',  110),
      makeItem('中 (Medium)', 80),
      makeItem('小 (Small)',  50),
      { type: 'separator' },
      { label: '穿透模式 (Click-through)', type: 'checkbox',
        checked: passthroughOn,
        click: () => {
          setPassthrough(!passthroughOn);
          refreshTrayMenu(getWindow());
        }},
      { label: '退出', click: () => { quitApp(); }},
    ]);

    menu.popup({ window: getWindow() });
  });

  // ── Config request (sync response) ───────────────────────
  ipcMain.on('get-config', (event) => {
    event.returnValue = getConfig();
  });

  // ── Save window position ──────────────────────────────────
  ipcMain.on('save-position', () => {
    const win = getWindow();
    if (!win) return;
    const [x, y] = win.getPosition();
    savePosition(x, y);
  });

  // ── Quit app (from keyboard shortcut or menu) ──────────────
  ipcMain.on('quit-app', () => {
    console.log('[main/ipc] quit-app requested');
    quitApp();
  });

  // ── Toggle passthrough (click-through) ─────────────────────
  ipcMain.on('toggle-passthrough', () => {
    const next = !isPassthrough();
    setPassthrough(next);
    refreshTrayMenu(getWindow());
  });

  console.log('[main/ipc] handlers registered');
}

module.exports = { registerIpcHandlers };
