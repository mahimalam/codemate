// electron/preload.js
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopApp', {
  isDesktop: true,
  platform: process.platform,
  version: process.env.npm_package_version || '2.6.0',
  sendNotification: (title, body) => {
    ipcRenderer.send('app-notification', { title, body });
  }
});
