const { app, BrowserWindow, Menu, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');

// App root directory
const ROOT_DIR = path.resolve(__dirname, '..');

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

const APP_NAME = branding.app?.name || 'VexP Code IDE';
const HOST = branding.server?.host || '127.0.0.1';
const PORT = branding.server?.port || 7860;
const SERVER_URL = `http://${HOST}:${PORT}`;

let mainWindow = null;
let pythonProcess = null;
let spawnedServer = false;
let isQuitting = false;

/**
 * Check if the backend server is reachable.
 */
function checkServerHealth() {
  return new Promise((resolve) => {
    const req = http.get(`${SERVER_URL}/api/version`, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(1500, () => {
      req.destroy();
      resolve(false);
    });
  });
}

/**
 * Start the Python backend process if not already running.
 */
async function startBackendServer() {
  const isRunning = await checkServerHealth();
  if (isRunning) {
    console.log(`[Electron] Detected existing server on ${SERVER_URL}`);
    return true;
  }

  console.log(`[Electron] Starting Python backend server...`);
  const pythonScript = path.join(ROOT_DIR, 'backend', 'server.py');
  const logFile = path.join(ROOT_DIR, 'server.log');
  const out = fs.openSync(logFile, 'a');

  pythonProcess = spawn('python3', [pythonScript], {
    cwd: ROOT_DIR,
    stdio: ['ignore', out, out],
    detached: false
  });

  spawnedServer = true;

  pythonProcess.on('error', (err) => {
    console.error('[Electron] Failed to spawn python backend:', err);
    dialog.showErrorBox(
      'Server Launch Error',
      `Failed to start backend/server.py:\n${err.message}\nPlease make sure Python 3 and dependencies are installed.`
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
      console.log(`[Electron] Python server ready on ${SERVER_URL}`);
      return true;
    }
    await new Promise((r) => setTimeout(r, 350));
  }

  throw new Error(`Server failed to start on ${SERVER_URL} within 20 seconds. Check server.log.`);
}

/**
 * Terminate the backend server if we spawned it.
 */
function stopBackendServer() {
  if (spawnedServer && pythonProcess && !pythonProcess.killed) {
    console.log('[Electron] Terminating spawned Python server...');
    try {
      pythonProcess.kill('SIGTERM');
      const proc = pythonProcess;
      setTimeout(() => {
        if (proc && !proc.killed) {
          try { proc.kill('SIGKILL'); } catch (_) {}
        }
      }, 3000);
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
              detail: `${branding.app?.tagline || 'Autonomous AI Coding Harness'}\nBackend: ${SERVER_URL}\nRunning on Linux Desktop (Electron)`
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
      webSecurity: true,
      spellcheck: false
    }
  });

  createMenu();

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();
  });

  // Load the web IDE
  mainWindow.loadURL(SERVER_URL);

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.warn(`[Electron] Page failed to load: ${errorDescription} (${errorCode})`);
    setTimeout(() => {
      if (!isQuitting && mainWindow) {
        mainWindow.loadURL(SERVER_URL);
      }
    }, 1500);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// App lifecycle
app.on('ready', async () => {
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
