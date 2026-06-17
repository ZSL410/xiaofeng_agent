// ═══════════════════════════════════════════════════════════
//  Mimic — Main Process Config Loader
// ═══════════════════════════════════════════════════════════

const path = require('path');
const fs   = require('fs');

const defaults = {
  targetDir: '../02_数据输入',
  backendUrl: 'http://127.0.0.1:5001',
  apiEnabled: false,
  defaultSize: 80,
  passthrough: false,
  musicDetect: true,
  musicCheckInterval: 3,
  musicPlayers: [],
};

let config = { ...defaults };

function loadConfig() {
  try {
    const configPath = path.join(__dirname, '..', 'config.json');
    const raw = fs.readFileSync(configPath, 'utf-8');
    config = { ...defaults, ...JSON.parse(raw) };
    console.log('[main/config] loaded:', configPath);
  } catch (err) {
    console.warn('[main/config] config.json not found, using defaults:', err.message);
  }
  return config;
}

function getConfig() {
  return config;
}

module.exports = { loadConfig, getConfig, defaults };
