import { app, BrowserWindow, ipcMain } from 'electron'
import { join } from 'node:path'
import type { AudioStatus, CoreStatus, DesktopSnapshot, RuntimeSummary } from '../shared/status'
import { CoreConnection } from './core-connection'
import { readRuntimeFile, resolveRuntimePath, type RuntimeFile } from './runtime-file'

const isSmokeRun = process.argv.includes('--smoke')
const SMOKE_TIMEOUT_MS = 12_000
const connection = new CoreConnection()
let mainWindow: BrowserWindow | null = null
let runtime: RuntimeFile | null = null
let rendererLoaded = false
let smokeFinished = false

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
  void window.loadFile(join(__dirname, '..', 'renderer', 'index.html'))
  return window
}

function registerIpc(): void {
  ipcMain.handle('copilot:snapshot', () => snapshot())
  ipcMain.handle('copilot:audio', () => readAudioStatus())
  ipcMain.handle('copilot:reconnect', async () => {
    runtime = readRuntimeFile()
    connection.setRuntime(runtime)
    await connection.connect()
    return snapshot()
  })
}

function maybeFinishSmoke(): void {
  if (!isSmokeRun || smokeFinished || !rendererLoaded) {
    return
  }
  const status: CoreStatus = connection.getStatus()
  if (status.phase === 'connected') {
    smokeFinished = true
    console.log(JSON.stringify({ smoke: 'connected', status }))
    app.exit(0)
  }
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
  runtime = readRuntimeFile()
  connection.setRuntime(runtime)
  registerIpc()

  mainWindow = createWindow()
  mainWindow.webContents.on('did-finish-load', () => {
    rendererLoaded = true
    maybeFinishSmoke()
  })

  connection.onChange((status) => {
    mainWindow?.webContents.send('copilot:core-status', status)
    maybeFinishSmoke()
  })

  startSmokeWatchdog()
  await connection.connect()
})

app.on('window-all-closed', () => {
  connection.disconnect()
  app.quit()
})
