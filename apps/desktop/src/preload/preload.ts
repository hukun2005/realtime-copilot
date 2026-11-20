import { contextBridge, ipcRenderer } from 'electron'
import type { CopilotBridge } from '../shared/bridge'
import type { CoreStatus } from '../shared/status'

/**
 * 唯一暴露给渲染进程的接口：固定四个方法，没有通用 `send`/`invoke`。
 * 通道名与主进程 `registerIpc()` 一一对应。
 */
const bridge: CopilotBridge = {
  snapshot: () => ipcRenderer.invoke('copilot:snapshot') as ReturnType<CopilotBridge['snapshot']>,
  audio: () => ipcRenderer.invoke('copilot:audio') as ReturnType<CopilotBridge['audio']>,
  reconnect: () => ipcRenderer.invoke('copilot:reconnect') as ReturnType<CopilotBridge['reconnect']>,
  onCoreStatus: (listener: (status: CoreStatus) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, status: CoreStatus): void => {
      listener(status)
    }
    ipcRenderer.on('copilot:core-status', handler)
    return () => {
      ipcRenderer.removeListener('copilot:core-status', handler)
    }
  },
}

contextBridge.exposeInMainWorld('copilot', bridge)
