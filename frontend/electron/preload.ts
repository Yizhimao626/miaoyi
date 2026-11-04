import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electron', {
  window: {
    minimize: () => ipcRenderer.invoke('window-minimize'),
    maximize: () => ipcRenderer.invoke('window-maximize'),
    close: () => ipcRenderer.invoke('window-close'),
  },
  theme: {
    get: () => ipcRenderer.invoke('get-theme'),
    set: (theme: 'light' | 'dark') => ipcRenderer.invoke('set-theme', theme),
  },
})