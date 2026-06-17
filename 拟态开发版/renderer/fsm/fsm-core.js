// ═══════════════════════════════════════════════════════════
//  Mimic — FSM Core Engine
//
//  States: idle | working | happy | surprised | alert
//  (v1.3.0: extracted from monolithic app.js)
//
//  Usage:
//    M.FSM.transitionTo('working');
//    M.FSM.update(performance.now());
//    const expr = M.FSM.getExpression();
//
//  Extending:
//    1. Add a new handler file in fsm/states/<name>.js
//    2. Add matching entry in EXPRESSIONS (fsm/expressions.js)
//    3. All drawing functions auto-render the new expression.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  const FSM = {
    state: 'idle',
    prevState: null,
    context: {},
    stateStartTime: 0,
    stateTimer: 0,

    transitionTo(newState, ctx = {}) {
      // Restart timer when re-entering the same non-idle state
      if (this.state === newState && newState !== 'idle') {
        this.stateStartTime = performance.now();
        this.context = { ...this.context, ...ctx };
        return;
      }

      this._exit(this.state);

      this.prevState = this.state;
      this.state = newState;
      this.context = ctx;
      this.stateStartTime = performance.now();
      this.stateTimer = 0;

      this._enter(newState);
      console.log('[fsm]', this.prevState, '→', newState,
                  Object.keys(ctx).length ? JSON.stringify(ctx) : '');
    },

    update(now) {
      this.stateTimer = now - this.stateStartTime;
      this._update(this.state, now);
    },

    getExpression() {
      const h = this._handlers[this.state];
      if (h && h.expression) {
        const name = (typeof h.expression === 'function')
          ? h.expression()
          : h.expression;
        return M.EXPRESSIONS[name] || M.EXPRESSIONS.neutral;
      }
      return M.EXPRESSIONS.neutral;
    },

    // ── State handlers (populated by fsm/states/*.js) ────

    _handlers: {},

    // ── Internal helpers ──────────────────────────────────

    _data: {},

    _enter(state) {
      const h = this._handlers[state];
      if (h && h.enter) h.enter.call(this);
    },

    _update(state, now) {
      const h = this._handlers[state];
      if (h && h.update) h.update.call(this, now);
    },

    _exit(state) {
      const h = this._handlers[state];
      if (h && h.exit) h.exit.call(this);
    },

    _startWorkAnim() {
      if (M.workTimer) return;
      M.workTimer = setInterval(M.Rendering.draw, 80);
    },

    _stopWorkAnim() {
      if (M.workTimer) { clearInterval(M.workTimer); M.workTimer = null; }
    },

    _clearTimers() {
      this._stopWorkAnim();
      this._clearTimer('happyTimer');
      this._clearTimer('surprisedTimer');
    },

    _clearTimer(key) {
      if (this._data[key]) {
        clearTimeout(this._data[key]);
        this._data[key] = null;
      }
    },
  };

  M.FSM = FSM;
  console.log('[fsm/core] FSM engine initialized');
})();
