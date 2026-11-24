import WebSocket from 'ws'
import { classifyIncoming, makeEvent } from '@realtime-copilot/protocol'
import type { CoreStatus } from '../shared/status'
import { INITIAL_CORE_STATUS, reduceCoreStatus, type CoreEvent } from './core-state'
import type { RuntimeFile } from './runtime-file'
import type { CaptureOptions, TranscriptEvent } from '../shared/bridge'
import { AnswerEventGate, isAnswerEvent, nextReconnectDelay, type AnswerEvent } from './answer-events'

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
  private readonly answerListeners = new Set<(event: AnswerEvent) => void>()
  private readonly transcriptListeners = new Set<(event: TranscriptEvent) => void>()
  private readonly answerGate = new AnswerEventGate()
  private answerSessionId: string | null = null
  private reconnectTimer: NodeJS.Timeout | null = null
  private reconnectAttempt = 0
  private manualClose = false
  private fatalError = false

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

  /** 订阅答案事件；返回取消函数。 */
  onAnswerEvent(listener: (event: AnswerEvent) => void): () => void {
    this.answerListeners.add(listener)
    return () => {
      this.answerListeners.delete(listener)
    }
  }

  /** 当前订阅的 session；用于 UI 切换。 */
  setAnswerSession(sessionId: string | null): void {
    if (sessionId === this.answerSessionId) return
    const previous = this.answerSessionId
    this.answerSessionId = sessionId
    if (this.socket && this.status.phase === 'connected') {
      if (previous) this.sendSubscribe('answer.unsubscribe', previous)
      if (sessionId) this.sendSubscribe('answer.subscribe', sessionId)
    }
  }

  onTranscriptEvent(listener: (event: TranscriptEvent) => void): () => void {
    this.transcriptListeners.add(listener)
    return () => {
      this.transcriptListeners.delete(listener)
    }
  }

  startCapture(options: CaptureOptions): void {
    if (!this.socket || this.status.phase !== 'connected') return
    this.socket.send(JSON.stringify(makeEvent('capture.start', {
      loopback: { deviceId: null, processId: null },
      mic: { enabled: options.mic, deviceId: null },
    })))
  }

  stopCapture(): void {
    if (!this.socket || this.status.phase !== 'connected') return
    this.socket.send(JSON.stringify(makeEvent('capture.stop', {})))
  }

  getAnswerSession(): string | null {
    return this.answerSessionId
  }

  /** 主动断开并停止自动重连。 */
  disconnect(): void {
    this.manualClose = true
    this.clearReconnect()
    this.closeSocket()
  }

  private closeSocket(): void {
    this.clearReadyTimer()
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.removeAllListeners()
      socket.close()
    }
  }

  private sendSubscribe(kind: 'answer.subscribe' | 'answer.unsubscribe', sessionId: string): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return
    this.socket.send(JSON.stringify(makeEvent(kind, { sessionId })))
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private scheduleReconnect(): void {
    if (this.manualClose || this.fatalError || this.reconnectTimer) return
    const delay = nextReconnectDelay(this.reconnectAttempt)
    this.reconnectAttempt += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      void this.connect()
    }, delay)
  }

  /** 建立连接；失败只反映在状态里，不抛异常。 */
  async connect(): Promise<void> {
    await Promise.resolve()
    this.manualClose = false
    this.fatalError = false
    this.clearReconnect()
    this.closeSocket()
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
      const detail = reason.toString() || `code ${code}`
      if (this.manualClose) return
      if (this.status.phase !== 'error') {
        this.update(
          this.status.phase === 'connecting'
            ? { kind: 'error', message: `core closed the connection: ${detail}` }
            : { kind: 'closed', reason: detail },
        )
      }
      this.scheduleReconnect()
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
        this.reconnectAttempt = 0
        if (this.answerSessionId) this.sendSubscribe('answer.subscribe', this.answerSessionId)
        return
      }
      case 'system.error': {
        if (["auth_failed", "protocol_version_unsupported"].includes(event.payload.code)) {
          this.fatalError = true
        }
        this.update({
          kind: 'error',
          message: `${event.payload.code}: ${event.payload.message}`,
        })
        socket.close()
        return
      }
      case 'asr.partial':
      case 'asr.final': {
        const update: TranscriptEvent = {
          type: event.type,
          sessionId: this.answerSessionId,
          source: event.payload.source,
          text: event.payload.text,
          isFinal: event.type === 'asr.final',
        }
        for (const listener of this.transcriptListeners) listener(update)
        return
      }
      case 'transcript.segment':
      case 'transcript.reset': {
        if (event.payload.sessionId) this.setAnswerSession(event.payload.sessionId)
        return
      }
      case 'ping': {
        socket.send(JSON.stringify(makeEvent('pong', { nonce: event.payload.nonce })))
        return
      }
      default:
        if (isAnswerEvent(event) && this.answerGate.accept(event)) {
          for (const listener of this.answerListeners) listener(event)
        }
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

