/**
 * RealTime Copilot 有线协议 v1 的 TypeScript 入口。
 *
 * 类型由 `schema/events.v1.json` 生成；本文件只提供运行时校验、ID 生成与事件构造。
 * 生成的文件位于 `src/generated/types.ts`，不要手改。
 *
 * @module @realtime-copilot/protocol
 */
import Ajv2020 from 'ajv/dist/2020.js'
import schema from '../schema/events.v1.json'
import type { EventV1 } from './generated/types.js'

export type * from './generated/types.js'

/** 当前协议版本。信封 `v` 必须等于该值。 */
export const PROTOCOL_VERSION = 1 as const

/** 协议允许的全部事件类型，按 schema 的 oneOf 顺序。 */
export const EVENT_TYPES = [
  'hello',
  'system.ready',
  'system.error',
  'ping',
  'pong',
  'audio.status',
  'capture.start',
  'capture.stop',
  'asr.status',
  'asr.partial',
  'asr.final',
  'asr.error',
] as const

/** 事件类型别名。 */
export type EventType = (typeof EVENT_TYPES)[number]

/** 按类型取出具体事件类型，例如 `EventOf<'ping'>`。 */
export type EventOf<T extends EventType> = Extract<EventV1, { type: T }>

/** 按类型取出 payload 类型。 */
export type PayloadOf<T extends EventType> = EventOf<T>['payload']

/** 稳定错误码，与 schema 的 ErrorCode 枚举一致。 */
export type ErrorCode =
  | 'auth_failed'
  | 'auth_timeout'
  | 'protocol_version_unsupported'
  | 'invalid_envelope'
  | 'invalid_payload'
  | 'unsupported_event'
  | 'invalid_role'
  | 'internal_error'

/**
 * 收到一条原始消息后的分类结果。
 *
 * 调用方按 `kind` 决定处理方式：`ok` 继续分发；`version_mismatch` 与 `invalid`
 * 属于协议违规，通常回错误后关闭连接；`unknown_type` 表示版本内未知事件，允许
 * 记录后忽略（前向兼容），也可回 `unsupported_event`。
 */
export type Classification =
  | { kind: 'ok'; event: EventV1 }
  | { kind: 'version_mismatch'; received: unknown }
  | { kind: 'unknown_type'; received: string }
  | { kind: 'invalid'; message: string; path?: string }

const ajv = new Ajv2020({ allErrors: true, strict: false })
const validateEvent = ajv.compile(schema)

/**
 * 生成事件 ID。
 * @returns 形如 `evt_<32 hex>` 的标识，满足 schema 的 EventId 约束。
 */
export function newEventId(): string {
  return `evt_${randomHex()}`
}

/**
 * 生成请求关联 ID。
 * @returns 形如 `req_<32 hex>` 的标识，满足 schema 的 RequestId 约束。
 */
export function newRequestId(): string {
  return `req_${randomHex()}`
}

function randomHex(): string {
  return globalThis.crypto.randomUUID().replace(/-/g, '')
}

/**
 * 构造一条符合协议的事件。
 * @param type - 事件类型。
 * @param payload - 该类型的 payload。
 * @param options - 可选的 requestId 与时间戳覆盖（测试用）。
 * @returns 完整信封。
 */
export function makeEvent<T extends EventType>(
  type: T,
  payload: PayloadOf<T>,
  options: { requestId?: string; ts?: number } = {},
): EventOf<T> {
  const envelope = {
    v: PROTOCOL_VERSION,
    type,
    eventId: newEventId(),
    ts: options.ts ?? Date.now(),
    payload,
    ...(options.requestId === undefined ? {} : { requestId: options.requestId }),
  }
  // 泛型 T 无法把 { type: T; payload: PayloadOf<T> } 关联到联合成员 EventOf<T>，
  // 这是 TypeScript 的相关联合限制；此处信封已由构造方式保证，故做一次定向断言。
  return envelope as EventV1 as EventOf<T>
}

/**
 * 对任意输入做协议分类，不抛异常。
 * @param raw - 已 JSON.parse 的输入。
 * @returns 分类结果。
 */
export function classifyIncoming(raw: unknown): Classification {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { kind: 'invalid', message: 'event must be a JSON object' }
  }
  const record = raw as Record<string, unknown>
  if (record.v !== PROTOCOL_VERSION) {
    return { kind: 'version_mismatch', received: record.v }
  }
  if (typeof record.type !== 'string') {
    return { kind: 'invalid', message: 'type must be a string', path: '/type' }
  }
  if (!(EVENT_TYPES as readonly string[]).includes(record.type)) {
    return { kind: 'unknown_type', received: record.type }
  }
  if (!validateEvent(raw)) {
    const first = validateEvent.errors?.[0]
    return {
      kind: 'invalid',
      message: first ? `${first.instancePath || '/'} ${first.message ?? 'invalid'}` : 'invalid event',
      ...(first?.instancePath ? { path: first.instancePath } : {}),
    }
  }
  return { kind: 'ok', event: raw as EventV1 }
}
