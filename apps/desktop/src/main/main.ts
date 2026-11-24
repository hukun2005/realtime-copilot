import { app, BrowserWindow, ipcMain, net, protocol } from 'electron'
import { join, normalize, sep } from 'node:path'
import { pathToFileURL } from 'node:url'
import type { AudioStatus, CoreStatus, DesktopSnapshot, RuntimeSummary } from '../shared/status'
import type { AnswerEvent, ResumeProjectPatch, ResumeProjectRecord, ResumeRecord } from '../shared/bridge'
import { CoreConnection } from './core-connection'
import { readRuntimeFile, resolveRuntimePath, type RuntimeFile } from './runtime-file'

/**
 * 渲染进程使用 `copilot://app/` 而不是 `file://`：
 * 自定义标准协议有真实 origin，`script-src 'self'` 才有意义，也避开 file:// 的 CSP 歧义。
 */
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'copilot',
    privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
  },
])

const RENDERER_ORIGIN = 'copilot://app/'
const isSmokeRun = process.argv.includes('--smoke')
const SMOKE_TIMEOUT_MS = 12_000
const connection = new CoreConnection()
let mainWindow: BrowserWindow | null = null
let runtime: RuntimeFile | null = null
let rendererLoaded = false
let smokeFinished = false
let unsubscribeAnswer: (() => void) | null = null

function summarizeRuntime(): RuntimeSummary {
  return {
    found: runtime !== null,
    path: resolveRuntimePath(),
    wsUrl: runtime?.wsUrl ?? null,
    mode: runtime?.mode ?? null,
    port: runtime?.port ?? null,
    version: runtime?.version ?? null,
  }
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/** 通过 Core 的 /healthz 读取当前连接角色，用于展示音频进程状态。 */
async function readAudioStatus(): Promise<AudioStatus> {
  if (!runtime) {
    return { connected: false, roles: {}, error: 'runtime.json not found' }
  }
  try {
    const response = await fetch(`http://127.0.0.1:${runtime.port}/healthz`, {
      signal: AbortSignal.timeout(1500),
    })
    if (!response.ok) {
      return { connected: false, roles: {}, error: `healthz returned ${response.status}` }
    }
    const body = (await response.json()) as { roles?: Record<string, number> }
    return { connected: true, roles: body.roles ?? {}, error: null }
  } catch (error) {
    return { connected: false, roles: {}, error: describe(error) }
  }
}

async function snapshot(): Promise<DesktopSnapshot> {
  return {
    core: connection.getStatus(),
    runtime: summarizeRuntime(),
    audio: await readAudioStatus(),
    appVersion: app.getVersion(),
  }
}

function rendererDirectory(): string {
  return join(__dirname, '..', 'renderer')
}

/** 把 `copilot://app/<path>` 映射到打包后的渲染产物；越界路径一律 403。 */
function registerRendererProtocol(): void {
  const root = normalize(rendererDirectory()) + sep
  protocol.handle('copilot', (request) => {
    const url = new URL(request.url)
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '')
    const target = normalize(join(root, relative === '' ? 'index.html' : relative))
    if (!target.startsWith(root)) {
      return new Response('forbidden', { status: 403 })
    }
    return net.fetch(pathToFileURL(target).toString())
  })
}

async function coreApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!runtime) {
    throw new Error('runtime.json not found')
  }
  const headers = new Headers(init.headers)
  headers.set('X-Copilot-Token', runtime.token)
  const response = await fetch(`http://127.0.0.1:${runtime.port}${path}`, { ...init, headers })
  if (!response.ok) {
    throw new Error(`Core API ${path} returned ${response.status}`)
  }
  return (await response.json()) as T
}

async function listResumes(): Promise<ResumeRecord[]> {
  return coreApi<ResumeRecord[]>('/api/resumes')
}

async function uploadResume(fileName: string, data: ArrayBuffer): Promise<ResumeRecord> {
  const form = new FormData()
  form.set('file', new Blob([data]), fileName)
  return coreApi<ResumeRecord>('/api/resumes', { method: 'POST', body: form })
}

async function activateResume(id: string): Promise<void> {
  await coreApi(`/api/resumes/${encodeURIComponent(id)}/activate`, { method: 'POST' })
}

async function deleteResume(id: string): Promise<void> {
  await coreApi(`/api/resumes/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

async function updateResumeProject(id: string, patch: ResumeProjectPatch): Promise<ResumeProjectRecord> {
  return coreApi<ResumeProjectRecord>(`/api/resume-projects/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 760,
    height: 600,
    show: !isSmokeRun,
    title: 'RealTime Copilot',
    backgroundColor: '#101114',
    webPreferences: {
      preload: join(__dirname, '..', 'preload', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  void window.loadURL(RENDERER_ORIGIN + 'index.html')
  return window
}

function registerIpc(): void {
  ipcMain.handle('copilot:snapshot', () => snapshot())
  ipcMain.handle('copilot:audio', () => readAudioStatus())
  ipcMain.handle('copilot:resumes', () => listResumes())
  ipcMain.handle('copilot:resume-upload', (_event, fileName: string, data: ArrayBuffer) => uploadResume(fileName, data))
  ipcMain.handle('copilot:resume-activate', (_event, id: string) => activateResume(id))
  ipcMain.handle('copilot:resume-delete', (_event, id: string) => deleteResume(id))
  ipcMain.handle('copilot:resume-project-update', (_event, id: string, patch: ResumeProjectPatch) => updateResumeProject(id, patch))
  ipcMain.handle('copilot:reconnect', async () => {
    runtime = readRuntimeFile()
    connection.setRuntime(runtime)
    await connection.connect()
    return snapshot()
  })
}

async function maybeFinishSmoke(): Promise<void> {
  if (!isSmokeRun || smokeFinished || !rendererLoaded || !mainWindow) {
    return
  }
  const status: CoreStatus = connection.getStatus()
  if (status.phase !== 'connected') {
    return
  }

  smokeFinished = true
  let rendered = false
  try {
    const text = (await mainWindow.webContents.executeJavaScript('document.body.textContent', true)) as string
    rendered = text.includes('Core 服务') && text.includes('已连接')
  } catch {
    rendered = false
  }

  console.log(JSON.stringify({ smoke: rendered ? 'connected' : 'ui-missing', rendered, status }))
  app.exit(rendered ? 0 : 1)
}

function startSmokeWatchdog(): void {
  if (!isSmokeRun) {
    return
  }
  setTimeout(() => {
    if (smokeFinished) {
      return
    }
    smokeFinished = true
    console.log(JSON.stringify({ smoke: 'timeout', status: connection.getStatus() }))
    app.exit(1)
  }, SMOKE_TIMEOUT_MS)
}

void app.whenReady().then(async () => {
  registerRendererProtocol()
  runtime = readRuntimeFile()
  connection.setRuntime(runtime)
  registerIpc()

  mainWindow = createWindow()
  if (isSmokeRun) {
    mainWindow.webContents.on('console-message', (...args: unknown[]) => {
      console.error('[renderer]', args.slice(1).join(' '))
    })
  }
  mainWindow.webContents.on('did-finish-load', () => {
    rendererLoaded = true
    void maybeFinishSmoke()
  })

  unsubscribeAnswer = connection.onAnswerEvent((event) => {
    mainWindow?.webContents.send('copilot:answer-event', event)
  })
  connection.onChange((status) => {
    mainWindow?.webContents.send('copilot:core-status', status)
    void maybeFinishSmoke()
  })

  startSmokeWatchdog()
  await connection.connect()
})

app.on('window-all-closed', () => {
  unsubscribeAnswer?.()
  unsubscribeAnswer = null
  connection.disconnect()
  app.quit()
})
