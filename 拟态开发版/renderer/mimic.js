// ═══════════════════════════════════════════════════════════
//  Mimic — Global namespace & shared state
//  Version: v2.1.0
//
//  All modules attach to window.Mimic (aliased as M).
//  Loading order: this file MUST load first.
// ═══════════════════════════════════════════════════════════

;(function () {
  const { ipcRenderer, webUtils } = require('electron');
  const fs   = require('fs');
  const path = require('path');
  const http = require('http');

  // ── Version ──────────────────────────────────────────────
  const VERSION = '3.7.1';

  // ── Config loader ────────────────────────────────────────
  let config = { targetDir: '../02_数据输入', backendUrl: 'http://127.0.0.1:5001', apiEnabled: false };
  try {
    config = require('../config.json');
    console.log('[mimic] config loaded:', JSON.stringify(config));
  } catch (err) {
    console.warn('[mimic] config.json not found, using defaults:', err.message);
  }

  // ── Layout constants ─────────────────────────────────────
  const SIDE_PAD      = 20;
  const GAP           = 5;
  const BOTTOM_MARGIN = 10;
  const ARROW_H       = 6;

  // ── Cross-platform target directory ──────────────────────
  const TARGET = path.resolve(__dirname, '..', config.targetDir);

  // ── Pet size (user-selectable) ───────────────────────────
  let petSize = 80;

  // ── Window & layout state (computed by Layout module) ────
  let winW  = 120;
  let winH  = 90;
  let petCX = 60;
  let petCY = 45;

  // ── Animation globals ────────────────────────────────────
  let bobOffset  = 0;
  let eyeOffsetX = 0;
  let eyeOffsetY = 0;   // v2.0: eye tracking vertical
  let eyeOpen    = true;
  let lastBlink  = performance.now();
  let bubbleTimer = null;
  let workTimer   = null;

  // ── Bubble metrics ───────────────────────────────────────
  let bubbleMetrics = { fontSize: 14, padV: 8, padH: 14 };
  let currentBubbleH = 0;

  // ── DOM references (set by app.js on boot) ───────────────
  let canvas  = null;
  let ctx     = null;
  let overlay = null;
  let bubbleEl = null;

  // ── Exports ──────────────────────────────────────────────
  const M = {
    VERSION,
    config,
    SIDE_PAD, GAP, BOTTOM_MARGIN, ARROW_H,
    TARGET,

    // Getters/setters for shared state
    get petSize() { return petSize; },
    set petSize(v) { petSize = v; },
    get winW() { return winW; },
    set winW(v) { winW = v; },
    get winH() { return winH; },
    set winH(v) { winH = v; },
    get petCX() { return petCX; },
    set petCX(v) { petCX = v; },
    get petCY() { return petCY; },
    set petCY(v) { petCY = v; },

    get bobOffset() { return bobOffset; },
    set bobOffset(v) { bobOffset = v; },
    get eyeOffsetX() { return eyeOffsetX; },
    set eyeOffsetX(v) { eyeOffsetX = v; },
    get eyeOffsetY() { return eyeOffsetY; },
    set eyeOffsetY(v) { eyeOffsetY = v; },
    get eyeOpen() { return eyeOpen; },
    set eyeOpen(v) { eyeOpen = v; },
    get lastBlink() { return lastBlink; },
    set lastBlink(v) { lastBlink = v; },
    get bubbleTimer() { return bubbleTimer; },
    set bubbleTimer(v) { bubbleTimer = v; },
    get workTimer() { return workTimer; },
    set workTimer(v) { workTimer = v; },

    get bubbleMetrics() { return bubbleMetrics; },
    set bubbleMetrics(v) { bubbleMetrics = v; },
    get currentBubbleH() { return currentBubbleH; },
    set currentBubbleH(v) { currentBubbleH = v; },

    get canvas() { return canvas; },
    set canvas(v) { canvas = v; },
    get ctx() { return ctx; },
    set ctx(v) { ctx = v; },
    get overlay() { return overlay; },
    set overlay(v) { overlay = v; },
    get bubbleEl() { return bubbleEl; },
    set bubbleEl(v) { bubbleEl = v; },

    // External libs
    ipc: ipcRenderer,
    webUtils,
    fs,
    path,
    http,

    // Module placeholders
    FSM: null,
    Layout: null,
    Bubble: null,
    Rendering: null,
    Interaction: null,
    Audio: null,
    Particles: null,
    _sleepPhase: 0,
  };

  window.Mimic = M;
  console.log('[mimic] global namespace initialized v' + VERSION);

  // ── Keyboard shortcuts ───────────────────────────────────
  window.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;

    // Ctrl+Q → quit app
    if (mod && e.key === 'q') {
      e.preventDefault();
      console.log('[mimic] Ctrl+Q → quit-app');
      ipcRenderer.send('quit-app');
      return;
    }

    // Ctrl+Shift+P → toggle passthrough (works when window has focus)
    if (mod && e.shiftKey && (e.key === 'p' || e.key === 'P')) {
      e.preventDefault();
      console.log('[mimic] Ctrl+Shift+P → toggle-passthrough');
      ipcRenderer.send('toggle-passthrough');
    }
  });
})();
