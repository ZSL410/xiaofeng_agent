// ═══════════════════════════════════════════════════════════
//  Mimic — Expression System
//  Composable eyes / mouth / effect definitions.
//  Adding a new expression is a single entry in this table.
//  Inspired by Bobby's mood-driven visual states.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const EXPRESSIONS = {
    neutral: {
      name: '平静',
      eyes: 'round',
      mouth: 'none',
      cheek: false,
    },
    happy: {
      name: '开心',
      eyes: 'arch',
      mouth: 'smile',
      cheek: true,
    },
    working: {
      name: '专注',
      eyes: 'round',
      mouth: 'none',
      cheek: false,
    },
    open: {
      name: '张嘴',
      eyes: 'round',
      mouth: 'open',
      cheek: false,
    },
    surprised: {
      name: '惊讶',
      eyes: 'wide',
      mouth: 'open',
      cheek: false,
    },
    sleepy: {
      name: '困倦',
      eyes: 'closed',
      mouth: 'none',
      cheek: false,
    },
    proud: {
      name: '骄傲',
      eyes: 'closed_arch',
      mouth: 'grin',
      cheek: true,
    },
    alert: {
      name: '提醒',
      eyes: 'sparkle',
      mouth: 'none',
      cheek: false,
    },
    sleeping: {
      name: '睡觉',
      eyes: 'closed',
      mouth: 'none',
      cheek: false,
    },
    bongo: {
      name: '敲击',
      eyes: 'arch',
      mouth: 'grin',
      cheek: true,
    },
    listening: {
      name: '听歌',
      eyes: 'closed_arch',
      mouth: 'none',
      cheek: true,
    },
  };

  M.EXPRESSIONS = EXPRESSIONS;
  console.log('[fsm/expressions] registered', Object.keys(EXPRESSIONS).length, 'expressions');
})();
