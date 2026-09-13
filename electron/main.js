const { app, BrowserWindow, Menu, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
const net = require('net');
const { spawn } = require('child_process');

// App root directory
const ROOT_DIR = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..');

// Load branding configuration
let branding = {};
try {
  const brandingPath = path.join(ROOT_DIR, 'config', 'branding.json');
  if (fs.existsSync(brandingPath)) {
    branding = JSON.parse(fs.readFileSync(brandingPath, 'utf8'));
  }
} catch (e) {
  console.warn('Could not read branding.json, using defaults:', e.message);
}

const APP_NAME = branding.app?.name || 'CodeMate';
const HOST = branding.server?.host || '127.0.0.1';
let port = branding.server?.port || 7860;
let serverUrl = `http://${HOST}:${port}`;
const SESSION_TOKEN = crypto.randomBytes(32).toString('base64url');
const SESSION_FINGERPRINT = crypto.createHash('sha256').update(SESSION_TOKEN).digest('hex').slice(0, 16);


let mainWindow = null;
let pythonProcess = null;
let spawnedServer = false;
let isQuitting = false;

/**
 * Check if the backend server is reachable.
 */
function checkServerHealth() {
  return new Promise((resolve) => {
    const req = http.get(`${serverUrl}/api/version`, { headers: { 'X-Codemate-Token': SESSION_TOKEN } }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        try { resolve(res.statusCode === 200 && JSON.parse(body).session_fingerprint === SESSION_FINGERPRINT); }
        catch (_) { resolve(false); }
      });
    });
    req.on('error', () => resolve(false));
    req.setTimeout(1500, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function selectAvailablePort(preferredPort) {
  return new Promise((resolve, reject) => {
    const preferred = net.createServer();
    preferred.once('error', () => {
      const fallback = net.createServer();
      fallback.once('error', reject);
      fallback.listen(0, HOST, () => {
        const address = fallback.address();
        const selected = typeof address === 'object' && address ? address.port : preferredPort;
        fallback.close(() => resolve(selected));
      });
    });
    preferred.listen(preferredPort, HOST, () => preferred.close(() => resolve(preferredPort)));
  });
}

/**
 * Detect Python executable cross-platform (Windows & Linux/macOS).
 */
function getPythonExecutable() {
  const isWin = process.platform === 'win32';
  const candidates = [
    isWin ? path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe') : path.join(ROOT_DIR, '.venv', 'bin', 'python'),
    isWin ? path.join(ROOT_DIR, 'venv', 'Scripts', 'python.exe') : path.join(ROOT_DIR, 'venv', 'bin', 'python')
  ];

  for (const p of candidates) {
    if (fs.existsSync(p)) {
      return p;
    }
  }

  // System python fallback
  return isWin ? 'python' : 'python3';
}

/**
 * Start the Python backend process if not already running.
 */
async function startBackendServer() {
  const isRunning = await checkServerHealth();
  if (isRunning) {
    console.log(`[Electron] Detected owned server on ${serverUrl}`);
    return true;
  }
  port = await selectAvailablePort(port);
  serverUrl = `http://${HOST}:${port}`;

  const pythonCmd = getPythonExecutable();
  console.log(`[Electron] Starting Python backend server using: ${pythonCmd}...`);
  const pythonScript = path.join(ROOT_DIR, 'backend', 'server.py');
  const logFile = path.join(app.getPath('userData'), 'server.log');
  const out = fs.openSync(logFile, 'a');

  pythonProcess = spawn(pythonCmd, [pythonScript], {
    cwd: ROOT_DIR,
    stdio: ['ignore', out, out],
    detached: false,
    shell: false,
    env: { ...process.env, HOST, PORT: String(port), CODEMATE_SESSION_TOKEN: SESSION_TOKEN }
  });
  fs.closeSync(out);

  spawnedServer = true;

  pythonProcess.on('error', (err) => {
    console.error('[Electron] Failed to spawn python backend:', err);
    dialog.showErrorBox(
      'Server Launch Error',
      `Failed to start backend/server.py with command "${pythonCmd}":\n${err.message}\nPlease make sure Python 3 and dependencies are installed.`
    );
  });

  pythonProcess.on('exit', (code, signal) => {
    console.log(`[Electron] Python server exited with code ${code}, signal ${signal}`);
    pythonProcess = null;
  });

  // Wait for server to become healthy (up to 20 seconds)
  const startTime = Date.now();
  while (Date.now() - startTime < 20000) {
    if (await checkServerHealth()) {
      console.log(`[Electron] Python server ready on ${serverUrl}`);
      return true;
    }
    await new Promise((r) => setTimeout(r, 350));
  }

  throw new Error(`Server failed to start on ${serverUrl} within 20 seconds. Check the application server log.`);
}

/**
 * Terminate the backend server if we spawned it.
 */
function stopBackendServer() {
  if (spawnedServer && pythonProcess && !pythonProcess.killed) {
    console.log('[Electron] Terminating spawned Python server...');
    try {
      if (process.platform === 'win32') {
        // Clean process-tree termination on Windows
        spawn('taskkill', ['/pid', pythonProcess.pid.toString(), '/T', '/F']);
      } else {
        pythonProcess.kill('SIGTERM');
        const proc = pythonProcess;
        setTimeout(() => {
          if (proc && !proc.killed) {
            try { proc.kill('SIGKILL'); } catch (_) {}
          }
        }, 3000);
      }
    } catch (e) {
      console.error('[Electron] Error killing python process:', e);
    }
    pythonProcess = null;
  }
}

/**
 * Build sleek native app menu.
 */
function createMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        { label: 'Reload IDE', accelerator: 'CmdOrCtrl+R', click: () => mainWindow?.reload() },
        { label: 'Force Reload', accelerator: 'CmdOrCtrl+Shift+R', click: () => mainWindow?.webContents.reloadIgnoringCache() },
        { type: 'separator' },
        { label: 'Quit', accelerator: 'CmdOrCtrl+Q', click: () => app.quit() }
      ]
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' }
      ]
    },
    {
      label: 'View',
      submenu: [
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        { label: 'Toggle Developer Tools', accelerator: 'F12', click: () => mainWindow?.webContents.toggleDevTools() }
      ]
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'About ' + APP_NAME,
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: APP_NAME,
              message: `${APP_NAME} v${branding.app?.version || '2.6.0'}`,
              detail: `${branding.app?.tagline || 'AI coding workspace with controlled agent tools'}\nBackend: ${serverUrl}`
            });
          }
        }
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

/**
 * Create the main IDE window.
 */
function createMainWindow() {
  const iconPath = path.join(ROOT_DIR, 'assets', 'icon.png');

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 640,
    title: APP_NAME,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    backgroundColor: '#090d16',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      devTools: !app.isPackaged,
      spellcheck: false
    }
  });

  createMenu();

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();
  });

  // Load the web IDE
  mainWindow.loadURL(serverUrl);
  mainWindow.webContents.session.setPermissionCheckHandler(() => false);
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://') || url.startsWith('http://')) require('electron').shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(`${serverUrl}/`) && url !== serverUrl) event.preventDefault();
  });

  mainWindow.webContents.on('context-menu', (_event, params) => {
    const items = [];
    if (params.isEditable) {
      items.push(
        { role: 'undo', enabled: params.editFlags.canUndo },
        { role: 'redo', enabled: params.editFlags.canRedo },
        { type: 'separator' },
        { role: 'cut', enabled: params.editFlags.canCut },
        { role: 'copy', enabled: params.editFlags.canCopy },
        { role: 'paste', enabled: params.editFlags.canPaste },
        { type: 'separator' },
        { role: 'selectAll', enabled: params.editFlags.canSelectAll }
      );
    } else if (params.selectionText) {
      items.push(
        { role: 'copy', enabled: params.editFlags.canCopy },
        { type: 'separator' },
        { role: 'selectAll' }
      );
    }
    if (params.linkURL) {
      if (items.length) items.push({ type: 'separator' });
      items.push(
        { label: 'Open Link in Browser', click: () => require('electron').shell.openExternal(params.linkURL) },
        { label: 'Copy Link Address', click: () => require('electron').clipboard.writeText(params.linkURL) }
      );
    }
    if (items.length) Menu.buildFromTemplate(items).popup({ window: mainWindow });
  });

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.warn(`[Electron] Page failed to load: ${errorDescription} (${errorCode})`);
    setTimeout(() => {
      if (!isQuitting && mainWindow) {
        mainWindow.loadURL(serverUrl);
      }
    }, 1500);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// App lifecycle
app.on('ready', async () => {
  ipcMain.handle('dialog:openDirectory', async () => {
    if (!mainWindow) return { canceled: true };
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
      title: 'Open Workspace Folder'
    });
    if (!result.canceled && result.filePaths && result.filePaths.length > 0) {
      return { canceled: false, path: result.filePaths[0] };
    }
    return { canceled: true };
  });

  try {
    await startBackendServer();
    createMainWindow();
  } catch (err) {
    console.error('[Electron] Failed to start IDE:', err);
    dialog.showErrorBox('Initialization Error', err.message);
    app.quit();
  }
});

app.on('window-all-closed', () => {
  isQuitting = true;
  stopBackendServer();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  stopBackendServer();
});

process.on('SIGINT', () => {
  stopBackendServer();
  process.exit(0);
});

process.on('SIGTERM', () => {
  stopBackendServer();
  process.exit(0);
});
