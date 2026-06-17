// ═══════════════════════════════════════════════════════════
//  Mimic v2.4 — Music Player Detector
//
//  Polls the OS process list for known music player executables.
//  When found → IPC 'music-playing' to renderer.
//  When lost  → IPC 'music-stopped' to renderer.
//  Configurable via config.json: musicDetect, musicPlayers.
// ═══════════════════════════════════════════════════════════

const { execSync } = require('child_process');

let checkInterval  = null;
let wasPlaying     = false;
let foundPlayer    = null;

// ── Default process names (case-insensitive, without .exe) ──
const DEFAULT_PLAYERS = [
  'spotify',
  'qqmusic',
  'cloudmusic',       // NetEase Cloud Music
  'netease',
  'kwmusic',          // Kuwo Music
  'kugou',
  'qmmp',             // Linux
  'rhythmbox',        // Linux
];

function startDetector(win) {
  let config;
  try {
    config = require('./config').getConfig();
  } catch (e) { return; }

  if (!config.musicDetect) {
    console.log('[music-detect] disabled in config');
    return;
  }

  const players = (config.musicPlayers && config.musicPlayers.length)
    ? config.musicPlayers
    : DEFAULT_PLAYERS;

  const interval = (config.musicCheckInterval || 3) * 1000;

  console.log('[music-detect] watching for:', players.join(', '),
              '| interval:', interval / 1000, 's');

  checkInterval = setInterval(() => {
    const found = scan(players);

    if (found && !wasPlaying) {
      wasPlaying = true;
      foundPlayer = found;
      console.log('[music-detect] DETECTED:', found, '→ sending music-playing');
      if (win && !win.isDestroyed()) {
        win.webContents.send('music-playing', found);
      }
    } else if (!found && wasPlaying) {
      wasPlaying = false;
      foundPlayer = null;
      console.log('[music-detect] STOPPED → sending music-stopped');
      if (win && !win.isDestroyed()) {
        win.webContents.send('music-stopped');
      }
    }
  }, interval);
}

function scan(players) {
  try {
    let output;
    if (process.platform === 'win32') {
      // Windows: tasklist returns running exe names
      output = execSync('tasklist /NH 2>&1', {
        encoding: 'utf8', timeout: 3000, windowsHide: true,
      });
    } else {
      // Linux / macOS
      output = execSync('ps aux 2>&1', {
        encoding: 'utf8', timeout: 3000,
      });
    }

    const lower = output.toLowerCase();
    for (const name of players) {
      const n = name.toLowerCase();
      if (lower.includes(n + '.exe') || lower.includes(n)) {
        return name;
      }
    }
  } catch (_) {
    // detection failed — silently ignore
  }
  return null;
}

function stopDetector() {
  if (checkInterval) {
    clearInterval(checkInterval);
    checkInterval = null;
    console.log('[music-detect] stopped');
  }
}

function isPlaying() {
  return wasPlaying;
}

function getPlayerName() {
  return foundPlayer;
}

module.exports = { startDetector, stopDetector, isPlaying, getPlayerName };
