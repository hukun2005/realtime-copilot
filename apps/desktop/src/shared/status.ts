/** 主进程、preload 与渲染进程共享的状态类型。 */

export type CorePhase = 'idle' | 'connecting' | 'connected' | 'closed' | 'error'

export interface CoreStatus {
  phase: CorePhase
  url: string | null
  serverVersion: string | null
  protocol: number | null
  mode: string | null
  error: string | null
  updatedAt: number
}

export interface RuntimeSummary {
  found: boolean
  path: string
  wsUrl: string | null
  mode: string | null
  port: number | null
  version: string | null
}

export interface AudioStatus {
  connected: boolean
  roles: Record<string, number>
  error: string | null
}

export interface AnswerStatus {
  configured: boolean
  state: string
  model: string | null
  lastError: { code: string; status: number | null; retryable: boolean } | null
  error: string | null
}

export interface DesktopSnapshot {
  core: CoreStatus
  runtime: RuntimeSummary
  audio: AudioStatus
  answer: AnswerStatus
  appVersion: string
}
