/* eslint-disable */
/**
 * AUTO-GENERATED from packages/protocol/schema/events.v1.json
 * 由 packages/protocol/scripts/generate.mjs 生成，请勿手改。
 * 变更协议请改 schema 后运行：pnpm -C packages/protocol generate
 */

/**
 * RealTime Copilot 有线协议 v1。JSON Schema 是唯一事实来源；TypeScript 类型与 Python Pydantic 模型由 scripts/generate.mjs 生成，C# DTO 手写并由 fixtures 往返测试约束。所有事件共用同一 envelope：v/type/eventId/requestId/ts/payload。
 */
export type EventV1 =
  | HelloEvent
  | SystemReadyEvent
  | SystemErrorEvent
  | PingEvent
  | PongEvent
  | AudioStatusEvent
  | CaptureStartEvent
  | CaptureStopEvent
/**
 * 事件唯一标识；由发送方生成，接收方只用于日志与关联，不承担幂等语义。
 */
export type EventId = string
/**
 * 请求关联标识；响应事件回填同一 requestId。
 */
export type RequestId = string
/**
 * 连接角色。audio 只能发送音频与控制帧；ui/bridge/tests 只能收发事件。
 */
export type Role = 'ui' | 'bridge' | 'audio' | 'tests'
/**
 * 稳定的机器可路由错误码；客户端不得依赖 message 文本。
 */
export type ErrorCode =
  | 'auth_failed'
  | 'auth_timeout'
  | 'protocol_version_unsupported'
  | 'invalid_envelope'
  | 'invalid_payload'
  | 'unsupported_event'
  | 'invalid_role'
  | 'not_implemented'
  | 'rate_limited'
  | 'internal_error'

export interface HelloEvent {
  v: 1
  type: 'hello'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: HelloPayload
}
export interface HelloPayload {
  token: string
  role: Role
  clientVersion: string
  clientName?: string
}
export interface SystemReadyEvent {
  v: 1
  type: 'system.ready'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: SystemReadyPayload
}
export interface SystemReadyPayload {
  serverVersion: string
  protocol: 1
  capabilities: string[]
  sessionId?: string | null
}
export interface SystemErrorEvent {
  v: 1
  type: 'system.error'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: SystemErrorPayload
}
export interface SystemErrorPayload {
  code: ErrorCode
  message: string
  retryable: boolean
  requestId?: string
}
export interface PingEvent {
  v: 1
  type: 'ping'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: HeartbeatPayload
}
export interface HeartbeatPayload {
  nonce?: string
}
export interface PongEvent {
  v: 1
  type: 'pong'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: HeartbeatPayload
}
export interface AudioStatusEvent {
  v: 1
  type: 'audio.status'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: AudioStatusPayload
}
export interface AudioStatusPayload {
  state: 'idle' | 'starting' | 'capturing' | 'stopping' | 'error'
  source?: 'loopback' | 'mic' | null
  deviceId?: string | null
  since: number
  error?: string | null
}
export interface CaptureStartEvent {
  v: 1
  type: 'capture.start'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: CaptureStartPayload
}
export interface CaptureStartPayload {
  loopback: LoopbackOptions
  mic: MicOptions
}
export interface LoopbackOptions {
  deviceId?: string | null
  processId?: number | null
}
export interface MicOptions {
  enabled: boolean
  deviceId?: string | null
}
export interface CaptureStopEvent {
  v: 1
  type: 'capture.stop'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: CaptureStopPayload
}
export interface CaptureStopPayload {}
