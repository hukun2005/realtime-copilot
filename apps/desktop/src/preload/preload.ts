import { contextBridge, ipcRenderer } from 'electron'
import type { AnswerEvent, CaptureOptions, CopilotBridge, ResumeProjectPatch, TranscriptEvent } from '../shared/bridge'
import type { CoreStatus } from '../shared/status'

/**
 * 唯一暴露给渲染进程的接口：固定四个方法，没有通用 `send`/`invoke`。
 * 通道名与主进程 `registerIpc()` 一一对应。
 */
const bridge: CopilotBridge = {
  snapshot: () => ipcRenderer.invoke('copilot:snapshot') as ReturnType<CopilotBridge['snapshot']>,
  audio: () => ipcRenderer.invoke('copilot:audio') as ReturnType<CopilotBridge['audio']>,
  reconnect: () => ipcRenderer.invoke('copilot:reconnect') as ReturnType<CopilotBridge['reconnect']>,
  resumes: () => ipcRenderer.invoke('copilot:resumes') as ReturnType<CopilotBridge['resumes']>,
  uploadResume: (fileName: string, data: ArrayBuffer) =>
    ipcRenderer.invoke('copilot:resume-upload', fileName, data) as ReturnType<CopilotBridge['uploadResume']>,
  activateResume: (id: string) =>
    ipcRenderer.invoke('copilot:resume-activate', id) as ReturnType<CopilotBridge['activateResume']>,
  deleteResume: (id: string) =>
    ipcRenderer.invoke('copilot:resume-delete', id) as ReturnType<CopilotBridge['deleteResume']>,
  updateResumeProject: (id: string, patch: ResumeProjectPatch) =>
    ipcRenderer.invoke('copilot:resume-project-update', id, patch) as ReturnType<CopilotBridge['updateResumeProject']>,
  startCapture: (options: CaptureOptions) =>
    ipcRenderer.invoke('copilot:capture-start', options) as ReturnType<CopilotBridge['startCapture']>,
  stopCapture: () => ipcRenderer.invoke('copilot:capture-stop') as ReturnType<CopilotBridge['stopCapture']>,
  cancelAnswer: (requestId: string) =>
    ipcRenderer.invoke('copilot:answer-cancel', requestId) as ReturnType<CopilotBridge['cancelAnswer']>,
  onTranscriptEvent: (listener: (event: TranscriptEvent) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, transcript: TranscriptEvent): void => {
      listener(transcript)
    }
    ipcRenderer.on('copilot:transcript-event', handler)
    return () => {
      ipcRenderer.removeListener('copilot:transcript-event', handler)
    }
  },
  onAnswerEvent: (listener: (event: AnswerEvent) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, answer: AnswerEvent): void => {
      listener(answer)
    }
    ipcRenderer.on('copilot:answer-event', handler)
    return () => {
      ipcRenderer.removeListener('copilot:answer-event', handler)
    }
  },
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
