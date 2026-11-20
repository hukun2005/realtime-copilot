import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/** runtime.json 的字段（由 Core 写入）。 */
export interface RuntimeFile {
  wsUrl: string
  token: string
  mode: string
  port: number
  version: string
  protocol: number
  path: string
}

/**
 * 解析 runtime.json 路径。
 *
 * @param env - 环境变量，默认 `process.env`。
 * @returns 绝对路径。
 */
export function resolveRuntimePath(env: NodeJS.ProcessEnv = process.env): string {
  if (env.RTC_RUNTIME_FILE) {
    return env.RTC_RUNTIME_FILE
  }
  if (env.RTC_DATA_DIR) {
    return join(env.RTC_DATA_DIR, 'runtime.json')
  }
  const localAppData = env.LOCALAPPDATA
  if (process.platform === 'win32' && localAppData) {
    return join(localAppData, 'RealTimeCopilot', 'runtime.json')
  }
  const dataHome = env.XDG_DATA_HOME ?? join(env.HOME ?? '.', '.local', 'share')
  return join(dataHome, 'realtime-copilot', 'runtime.json')
}

/**
 * 读取并校验 runtime.json。
 *
 * @param path - 显式路径，缺省用 {@link resolveRuntimePath}。
 * @returns 解析结果；文件缺失或格式非法时为 `null`。
 */
export function readRuntimeFile(path: string = resolveRuntimePath()): RuntimeFile | null {
  let text: string
  try {
    text = readFileSync(path, 'utf8')
  } catch {
    return null
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return null
  }

  if (typeof parsed !== 'object' || parsed === null) {
    return null
  }

  const record = parsed as Record<string, unknown>
  const wsUrl = record.wsUrl
  const token = record.token
  const port = record.port
  if (typeof wsUrl !== 'string' || typeof token !== 'string' || typeof port !== 'number') {
    return null
  }

  return {
    wsUrl,
    token,
    port,
    mode: typeof record.mode === 'string' ? record.mode : 'unknown',
    version: typeof record.version === 'string' ? record.version : 'unknown',
    protocol: typeof record.protocol === 'number' ? record.protocol : 0,
    path,
  }
}
