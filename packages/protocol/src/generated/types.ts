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
  | AsrStatusEvent
  | AsrPartialEvent
  | AsrFinalEvent
  | AsrErrorEvent
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
  | 'invalid_state'
  | 'capture_timeout'
  | 'sidecar_unavailable'
  | 'ticket_invalid'
  | 'asr_failed'
  | 'asr_timeout'
  | 'asr_unavailable'
  | 'asr_segment_conflict'
/**
 * ASR 连接状态；与采集状态机完全独立。
 */
export type AsrState = 'idle' | 'connecting' | 'ready' | 'error'
/**
 * 识别片段 ID；partial 与 final 共用同一 ID。
 */
export type AsrSegmentId = string
/**
 * 识别来源；与采集来源枚举保持一致，但独立演进。
 */
export type AsrSource = 'loopback' | 'mic'

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
  ticket?: string | null
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
  mode?: 'loopback' | 'mic' | 'process' | null
  since: number
  error?: string | null
  errorCode?: string | null
  sources?: {
    loopback?: AudioSourceStats | null
    mic?: AudioSourceStats | null
  }
}
export interface AudioSourceStats {
  framesReceived: number
  framesDropped: number
  duplicates: number
  invalid: number
  peakDbfs?: number | null
  rmsDbfs?: number | null
  silenceDurationMs: number
  lastFrameTimestamp?: number | null
  currentSampleRate?: number | null
  currentChannels?: number | null
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
export interface AsrStatusEvent {
  v: 1
  type: 'asr.status'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: AsrStatusPayload
}
export interface AsrStatusPayload {
  state: AsrState
  since: number
  provider?: string | null
  errorCode?: ErrorCode | null
  error?: string | null
}
export interface AsrPartialEvent {
  v: 1
  type: 'asr.partial'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: AsrPartialPayload
}
export interface AsrPartialPayload {
  segmentId: AsrSegmentId
  source: AsrSource
  /**
   * 采集时间轴起点（纳秒）。
   */
  startTsNs: number
  /**
   * 采集时间轴终点（纳秒）。
   */
  endTsNs: number
  revision: number
  text: string
  confidence?: number | null
  provider?: string | null
  language?: string | null
}
export interface AsrFinalEvent {
  v: 1
  type: 'asr.final'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: AsrFinalPayload
}
export interface AsrFinalPayload {
  segmentId: AsrSegmentId
  source: AsrSource
  /**
   * 采集时间轴起点（纳秒）。
   */
  startTsNs: number
  /**
   * 采集时间轴终点（纳秒）。
   */
  endTsNs: number
  revision: number
  text: string
  confidence?: number | null
  provider?: string | null
  language?: string | null
}
export interface AsrErrorEvent {
  v: 1
  type: 'asr.error'
  eventId: EventId
  requestId?: RequestId
  ts: number
  payload: AsrErrorPayload
}
export interface AsrErrorPayload {
  code: ErrorCode
  message: string
  segmentId?: AsrSegmentId | null
  retryable: boolean
}
