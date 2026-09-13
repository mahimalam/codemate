// electron/preload.js
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopApp', {
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory')
});
