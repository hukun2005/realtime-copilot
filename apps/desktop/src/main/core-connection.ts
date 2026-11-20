import WebSocket from 'ws'
import { classifyIncoming, makeEvent } from '@realtime-copilot/protocol'
import type { CoreStatus } from '../shared/status'
import { INITIAL_CORE_STATUS, reduceCoreStatus, type CoreEvent } from './core-state'
import type { RuntimeFile } from './runtime-file'

const HANDSHAKE_TIMEOUT_MS = 5000

/**
 * 桌面端到 Core 的连接：读 runtime.json、hello 鉴权、心跳应答与断线识别。
 *
 * 状态变化通过 {@link onChange} 推给窗口，渲染进程不直接持有 socket。
 */
export class CoreConnection {
  private status: CoreStatus = INITIAL_CORE_STATUS
  private socket: WebSocket | null = null
  private readyTimer: NodeJS.Timeout | null = null
  private runtime: RuntimeFile | null = null
  private readonly listeners = new Set<(status: CoreStatus) => void>()

  /** 注入本次运行读取到的 runtime 信息。 */
  setRuntime(runtime: RuntimeFile | null): void {
    this.runtime = runtime
  }

  /** 当前状态快照。 */
  getStatus(): CoreStatus {
    return this.status
  }

  /** 订阅状态变化。 */
  onChange(listener: (status: CoreStatus) => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** 断开当前连接（不改变状态）。 */
  disconnect(): void {
    this.clearReadyTimer()
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.removeAllListeners()
      socket.close()
    }
  }

  /** 建立连接；失败只反映在状态里，不抛异常。 */
  async connect(): Promise<void> {
    await Promise.resolve()
    this.disconnect()
    const runtime = this.runtime
    if (!runtime) {
      this.update({ kind: 'error', message: 'runtime.json not found; start the core first' })
      return
    }

    this.update({ kind: 'connecting', url: runtime.wsUrl })
    const socket = new WebSocket(runtime.wsUrl, { handshakeTimeout: HANDSHAKE_TIMEOUT_MS })
    this.socket = socket
    this.readyTimer = setTimeout(() => {
      this.update({ kind: 'error', message: 'core did not answer hello in time' })
      socket.terminate()
    }, HANDSHAKE_TIMEOUT_MS)

    socket.on('open', () => {
      socket.send(
        JSON.stringify(
          makeEvent('hello', {
            token: runtime.token,
            role: 'ui',
            clientVersion: '0.1.0',
            clientName: 'desktop',
          }),
        ),
      )
    })

    socket.on('message', (data: WebSocket.RawData) => {
      this.handleMessage(socket, data.toString())
    })

    socket.on('close', (code: number, reason: Buffer) => {
      this.clearReadyTimer()
      if (this.status.phase === 'error') {
        return
      }
      const detail = reason.toString() || `code ${code}`
      if (this.status.phase === 'connecting') {
        this.update({ kind: 'error', message: `core closed the connection: ${detail}` })
      } else {
        this.update({ kind: 'closed', reason: detail })
      }
    })

    socket.on('error', (error: Error) => {
      this.clearReadyTimer()
      this.update({ kind: 'error', message: error.message })
    })
  }

  private handleMessage(socket: WebSocket, text: string): void {
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      return
    }

    const result = classifyIncoming(parsed)
    if (result.kind !== 'ok') {
      // 前向兼容：未知事件忽略；版本/结构问题记为错误。
      if (result.kind === 'unknown_type') {
        return
      }
      this.update({ kind: 'error', message: `unexpected message from core: ${result.kind}` })
      return
    }

    const event = result.event
    switch (event.type) {
      case 'system.ready': {
        this.clearReadyTimer()
        this.update({
          kind: 'ready',
          serverVersion: event.payload.serverVersion,
          protocol: event.payload.protocol,
          mode: this.runtime?.mode ?? null,
        })
        return
      }
      case 'system.error': {
        this.update({
          kind: 'error',
          message: `${event.payload.code}: ${event.payload.message}`,
        })
        socket.close()
        return
      }
      case 'ping': {
        socket.send(JSON.stringify(makeEvent('pong', { nonce: event.payload.nonce })))
        return
      }
      default:
        return
    }
  }

  private update(event: CoreEvent): void {
    this.status = reduceCoreStatus(this.status, event)
    for (const listener of this.listeners) {
      listener(this.status)
    }
  }

  private clearReadyTimer(): void {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }
  }
}
