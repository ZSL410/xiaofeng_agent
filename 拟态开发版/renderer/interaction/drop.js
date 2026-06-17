// ═══════════════════════════════════════════════════════════
//  Mimic — File Drop Handler
//
//  Drag-and-drop files/folders from Windows Explorer onto
//  the pet window → recursive copy to target directory
//  → visual feedback (FSM state changes + bubble + overlay)
//  → backend API notification.
// ═══════════════════════════════════════════════════════════

;(function () {
  const M = window.Mimic;

  let enterCount = 0;

  // ── Recursive copy ──────────────────────────────────────

  function copyRecursive(srcPath, destDir) {
    const name = M.path.basename(srcPath);
    const dest = M.path.join(destDir, name);

    let st;
    try {
      st = M.fs.statSync(srcPath);
    } catch (err) {
      console.error('[copy] stat error:', srcPath, err.message);
      return 0;
    }

    if (st.isFile()) {
      M.fs.mkdirSync(M.path.dirname(dest), { recursive: true });
      M.fs.copyFileSync(srcPath, dest);
      console.log('[copy] file:', srcPath, '->', dest);
      return 1;
    }

    if (st.isDirectory()) {
      M.fs.mkdirSync(dest, { recursive: true });
      let count = 0;
      let entries;
      try {
        entries = M.fs.readdirSync(srcPath, { withFileTypes: true });
      } catch (err) {
        console.error('[copy] readdir error:', srcPath, err.message);
        return 0;
      }
      for (const entry of entries) {
        count += copyRecursive(M.path.join(srcPath, entry.name), dest);
      }
      return count;
    }

    return 0;
  }

  // ── Backend API call ────────────────────────────────────

  function notifyBackend(fileCount, filePaths) {
    if (!M.config.apiEnabled) {
      console.log('[api] disabled in config, skip');
      return;
    }

    const payload = JSON.stringify({
      count: fileCount,
      paths: filePaths,
      timestamp: new Date().toISOString(),
    });

    let url;
    try {
      url = new URL(M.config.backendUrl + '/api/file/process');
    } catch (err) {
      console.error('[api] invalid backendUrl:', M.config.backendUrl, err.message);
      return;
    }

    const options = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
      timeout: 3000,
    };

    console.log('[api] POST', url.href, '| payload:', payload);

    const req = M.http.request(options, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        console.log('[api] response', res.statusCode, '| body:', body);
      });
    });

    req.on('error', (err) => {
      console.log('[api] backend not reachable:', err.message, '(ignored)');
    });

    req.write(payload);
    req.end();
  }

  // ── Path extraction ─────────────────────────────────────

  function getFilePaths(fileList) {
    const paths = [];

    for (let i = 0; i < fileList.length; i++) {
      const file = fileList[i];

      console.log('[drop] file', i, '| name:', file.name, '| size:', file.size,
                  '| type:', file.type, '| path:', file.path);

      let p = '';
      try { p = M.webUtils.getPathForFile(file); } catch (e) {}

      if (!p && file.path) {
        p = file.path;
        console.log('[drop] using legacy file.path:', p);
      }

      if (p) {
        paths.push(p);
        console.log('[drop] resolved path:', p);
      } else {
        console.warn('[drop] WARNING: no path for file:', file.name,
                     '(webUtils returned empty, file.path is:', file.path, ')');
      }
    }

    return paths;
  }

  // ── Drop handler ────────────────────────────────────────

  function handleDrop(e) {
    const overlay = M.overlay;
    if (overlay) overlay.classList.remove('on');
    enterCount = 0;

    const files = e.dataTransfer.files;
    console.log('[drop] ======== DROP EVENT ========');
    console.log('[drop] files:', files ? files.length : 'null',
                '| items:', e.dataTransfer.items ? e.dataTransfer.items.length : 'null',
                '| types:', e.dataTransfer.types);

    if (!files || files.length === 0) {
      console.warn('[drop] no files');
      M.FSM.transitionTo('surprised');
      M.Bubble.show('(・_・?) 未放入任何文件');
      return;
    }

    const paths = getFilePaths(files);
    console.log('[drop] collected', paths.length, 'path(s):', paths);

    if (paths.length === 0) {
      console.error('[drop] FAILED: zero paths');
      M.FSM.transitionTo('surprised');
      M.Bubble.show('(>_<) 无法读取文件路径');
      return;
    }

    // Transition to working state
    M.FSM.transitionTo('working');
    M.Bubble.show('(*´▽`*) 正在复制…', 0);

    setTimeout(() => {
      try {
        console.log('[drop] ensuring target dir:', M.TARGET);
        M.fs.mkdirSync(M.TARGET, { recursive: true });

        let total = 0;
        for (const p of paths) {
          total += copyRecursive(p, M.TARGET);
        }

        // Transition to happy state
        M.FSM.transitionTo('happy');
        M.Bubble.show("(●'◡'●) 已复制 " + total + ' 个文件', 3000);
        console.log('[drop] SUCCESS: copied', total, 'file(s) to', M.TARGET);
        notifyBackend(total, paths);

      } catch (err) {
        M.FSM.transitionTo('surprised');
        M.Bubble.show('(╥﹏╥) 错误', 3000);
        console.error('[drop] ERROR:', err.message, err);
      }
    }, 60);
  }

  // ── Register drop targets ───────────────────────────────

  function registerDropTarget(el) {
    el.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.stopPropagation();
    });

    el.addEventListener('dragenter', (e) => {
      e.preventDefault();
      e.stopPropagation();
      enterCount++;
      if (enterCount === 1 && M.overlay) M.overlay.classList.add('on');
    });

    el.addEventListener('dragleave', (e) => {
      e.preventDefault();
      e.stopPropagation();
      enterCount--;
      if (enterCount <= 0) {
        enterCount = 0;
        if (M.overlay) M.overlay.classList.remove('on');
      }
    });

    el.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      handleDrop(e);
    });
  }

  function setupDrop() {
    registerDropTarget(document);
    const canvas = M.canvas;
    if (canvas) registerDropTarget(canvas);
    console.log('[drop] file drop handlers registered | target:', M.TARGET);
  }

  M.Interaction = M.Interaction || {};
  M.Interaction.setupDrop = setupDrop;
  console.log('[interaction/drop] module initialized');
})();
