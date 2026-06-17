// ═══════════════════════════════════════════════════════════
//  Mimic — Desktop Pet 主进程入口
//  Version: v2.5.0 (Phase 3 — passthrough + particles + auto music detect)
//
//  Boot sequence: config → window → tray → IPC → global shortcuts
//  All sub-modules live in main/ directory.
// ═══════════════════════════════════════════════════════════

const { app, globalShortcut } = require('electron');
const { loadConfig, getConfig } = require('./main/config');
const { createWindow, getWindow, setPassthrough, isPassthrough } = require('./main/window');
const { createTray } = require('./main/tray');
const { registerIpcHandlers } = require('./main/ipc-handlers');
const { refreshTrayMenu } = require('./main/tray');
const { startDetector, stopDetector } = require('./main/music-detector');

app.whenReady().then(() => {
  // 1. Load configuration
  const config = loadConfig();

  // 2. Create the transparent desktop pet window
  const win = createWindow();

  // 3. Set up system tray
  createTray(win);

  // 4. Register all IPC handlers
  registerIpcHandlers();

  // 5. Apply passthrough from config (if enabled)
  if (config.passthrough) {
    setPassthrough(true);
    refreshTrayMenu(win);
  }

  // 6. Register global shortcut: Ctrl+Shift+P toggles passthrough
  globalShortcut.register('CmdOrCtrl+Shift+P', () => {
    const next = !isPassthrough();
    setPassthrough(next);
    refreshTrayMenu(getWindow());
    console.log('[main] global shortcut: passthrough →', next ? 'ON' : 'OFF');
  });

  // 7. Start music player detector
  startDetector(win);

  console.log('[main] 拟态 Desktop Pet ready');
});

app.on('window-all-closed', () => {
  // Don't quit — close is intercepted, window just hides
});

app.on('will-quit', () => {
  // Clean up global shortcuts and detector
  globalShortcut.unregisterAll();
  stopDetector();
});
