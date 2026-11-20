import type { CoreStatus } from '../shared/status'

/** Core 连接状态机可以接收的事件。 */
export type CoreEvent =
  | { kind: 'connecting'; url: string }
  | { kind: 'ready'; serverVersion: string; protocol: number; mode: string | null }
  | { kind: 'closed'; reason: string }
  | { kind: 'error'; message: string }

/** 初始状态。 */
export const INITIAL_CORE_STATUS: CoreStatus = {
  phase: 'idle',
  url: null,
  serverVersion: null,
  protocol: null,
  mode: null,
  error: null,
  updatedAt: 0,
}

/**
 * 纯函数状态机：同一个事件序列在任何进程里都得到同样的状态，便于测试。
 *
 * @param status - 当前状态。
 * @param event - 新事件。
 * @param now - 事件时间戳（测试注入）。
 * @returns 新状态；`connected` 之后收到 `closed` 会保留版本信息以便排查。
 */
export function reduceCoreStatus(status: CoreStatus, event: CoreEvent, now: number = Date.now()): CoreStatus {
  switch (event.kind) {
    case 'connecting':
      return { ...status, phase: 'connecting', url: event.url, error: null, updatedAt: now }
    case 'ready':
      return {
        ...status,
        phase: 'connected',
        serverVersion: event.serverVersion,
        protocol: event.protocol,
        mode: event.mode,
        error: null,
        updatedAt: now,
      }
    case 'closed':
      return { ...status, phase: 'closed', error: event.reason, updatedAt: now }
    case 'error':
      return {
        ...status,
        phase: 'error',
        serverVersion: null,
        protocol: null,
        error: event.message,
        updatedAt: now,
      }
  }
}
