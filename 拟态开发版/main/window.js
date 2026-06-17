// ═══════════════════════════════════════════════════════════
//  Mimic v2.1 — Window Manager
//
//  Creates the transparent, frameless, always-on-top window.
//  Restores saved position. Saves position on close.
// ═══════════════════════════════════════════════════════════

const { BrowserWindow } = require('electron');
const path = require('path');
const { loadPosition, savePosition } = require('./position-store');

let win = null;
let forceQuit = false;
let passthroughEnabled = false;

function createWindow() {
  win = new BrowserWindow({
    width: 120,
    height: 90,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      sandbox: false,
    },
  });

  win.setMenu(null);

  // Restore saved position
  const saved = loadPosition();
  if (saved) {
    win.setPosition(saved.x, saved.y);
    console.log('[main/window] restored position:', saved);
  }

  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  // Save position on close (minimise to tray); allow real quit when forceQuit=true
  win.on('close', (e) => {
    // Always save position before closing/hiding
    const [x, y] = win.getPosition();
    savePosition(x, y);

    if (forceQuit) {
      // Allow the window to actually close
      win = null;
      return;
    }

    e.preventDefault();
    win.hide();
  });

  console.log('[main/window] created');
  return win;
}

function getWindow() {
  return win;
}

function quitApp() {
  forceQuit = true;
  if (win) {
    // Save position one last time
    const [x, y] = win.getPosition();
    savePosition(x, y);
    win.close();
  }
  // If no window, just exit
  require('electron').app.exit(0);
}

// ── Passthrough (click-through) control ─────────────────────
function setPassthrough(enabled) {
  if (!win) return;
  passthroughEnabled = !!enabled;
  win.setIgnoreMouseEvents(passthroughEnabled, { forward: true });
  console.log('[main/window] passthrough:', passthroughEnabled ? 'ON (click-through)' : 'OFF (interactive)');
}

function isPassthrough() {
  return passthroughEnabled;
}

module.exports = { createWindow, getWindow, quitApp, setPassthrough, isPassthrough };
