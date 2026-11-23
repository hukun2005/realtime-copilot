import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  EVENT_TYPES,
  PROTOCOL_VERSION,
  classifyIncoming,
  makeEvent,
  newEventId,
  newRequestId,
} from '../src/index.ts'
import type { EventV1 } from '../src/index.ts'

const testDir = dirname(fileURLToPath(import.meta.url))
const fixturesDir = join(testDir, '..', 'fixtures')
const schemaPath = join(testDir, '..', 'schema', 'events.v1.json')

function readJson(path: string): unknown {
  return JSON.parse(readFileSync(path, 'utf8'))
}

function validFixtureNames(): string[] {
  return readdirSync(fixturesDir)
    .filter((name) => name.endsWith('.json'))
    .sort()
}

describe('协议 fixtures', () => {
  it('至少覆盖全部 8 种事件', () => {
    expect(validFixtureNames()).toHaveLength(EVENT_TYPES.length)
  })

  it.each(validFixtureNames())('%s 通过校验且分类为 ok', (name) => {
    const raw = readJson(join(fixturesDir, name))
    const result = classifyIncoming(raw)
    expect(result.kind).toBe('ok')
    if (result.kind === 'ok') {
      expect(result.event.type).toBe(name.replace('.json', ''))
    }
  })

  it('往返序列化不丢字段', () => {
    for (const name of validFixtureNames()) {
      const raw = readJson(join(fixturesDir, name))
      const first = classifyIncoming(raw)
      expect(first.kind).toBe('ok')
      if (first.kind !== 'ok') continue

      const wire = JSON.parse(JSON.stringify(first.event)) as unknown
      expect(wire).toEqual(raw)

      const second = classifyIncoming(wire)
      expect(second.kind).toBe('ok')
      if (second.kind === 'ok') {
        expect(second.event).toEqual(first.event)
      }
    }
  })
})

describe('非法输入', () => {
  const invalidDir = join(fixturesDir, 'invalid')

  it('未知事件类型被识别为 unknown_type', () => {
    const result = classifyIncoming(readJson(join(invalidDir, 'unknown-type.json')))
    expect(result).toEqual({ kind: 'unknown_type', received: 'meeting.summary' })
  })

  it('协议版本不兼容被单独识别', () => {
    const result = classifyIncoming(readJson(join(invalidDir, 'bad-version.json')))
    expect(result).toEqual({ kind: 'version_mismatch', received: 2 })
  })

  it.each(['missing-payload.json', 'extra-envelope-field.json', 'wrong-role.json'])(
    '%s 被判为 invalid 并给出路径',
    (name) => {
      const result = classifyIncoming(readJson(join(invalidDir, name)))
      expect(result.kind).toBe('invalid')
      if (result.kind === 'invalid') {
        expect(result.message.length).toBeGreaterThan(0)
      }
    },
  )

  it('非对象输入被拒绝', () => {
    expect(classifyIncoming('hello').kind).toBe('invalid')
    expect(classifyIncoming(null).kind).toBe('invalid')
    expect(classifyIncoming([]).kind).toBe('invalid')
  })
})

describe('信封一致性', () => {
  const schema = readJson(schemaPath) as {
    $defs: Record<string, { properties?: Record<string, unknown>; required?: string[]; title?: string }>
    oneOf: { $ref: string }[]
  }

  const envelopeKeys = ['v', 'type', 'eventId', 'requestId', 'ts', 'payload']
  const answerKeys = [...envelopeKeys, 'sessionId', 'questionId', 'generation', 'sequence']
  const answerTypes = new Set([
    'answer.started', 'answer.delta', 'answer.completed', 'answer.cancelled', 'answer.failed',
  ])

  it('每个事件定义使用同一组信封字段', () => {
    const eventDefs = Object.entries(schema.$defs).filter(([key]) => key.endsWith('Event'))
    expect(eventDefs).toHaveLength(EVENT_TYPES.length)

    for (const [key, def] of eventDefs) {
      const eventType = String((def.properties?.type as { const?: string } | undefined)?.const)
      if (eventType === 'answer.subscribe' || eventType === 'answer.unsubscribe') {
        expect(def.required, key).toEqual(['v', 'type', 'eventId', 'requestId', 'ts', 'payload'])
        expect(Object.keys(def.properties ?? {}).every((name) => envelopeKeys.includes(name)), key).toBe(true)
      } else if (answerTypes.has(eventType)) {
        expect(def.required, key).toEqual([
          'v', 'type', 'eventId', 'requestId', 'sessionId',
          'questionId', 'generation', 'sequence', 'ts', 'payload',
        ])
        expect(Object.keys(def.properties ?? {}).every((name) => answerKeys.includes(name)), key).toBe(true)
      } else {
        expect(def.required, key).toEqual(['v', 'type', 'eventId', 'ts', 'payload'])
        expect(Object.keys(def.properties ?? {}).every((name) => envelopeKeys.includes(name)), key).toBe(true)
      }
      expect(def.properties?.v, key).toEqual({ const: PROTOCOL_VERSION })
    }
  })

  it('oneOf 顺序与 EVENT_TYPES 一致', () => {
    const referenced = schema.oneOf.map((entry) => {
      const name = entry.$ref.split('/').pop() ?? ''
      const def = schema.$defs[name]
      return String((def?.properties?.type as { const?: string } | undefined)?.const)
    })
    expect(referenced).toEqual([...EVENT_TYPES])
  })
})

describe('ID 与构造', () => {
  it('事件 ID 满足 pattern 且不重复', () => {
    const ids = new Set<string>()
    for (let index = 0; index < 1000; index += 1) {
      const id = newEventId()
      expect(id).toMatch(/^evt_[A-Za-z0-9_-]{8,64}$/)
      ids.add(id)
    }
    expect(ids.size).toBe(1000)
  })

  it('请求 ID 满足 pattern', () => {
    expect(newRequestId()).toMatch(/^req_[A-Za-z0-9_-]{8,64}$/)
  })

  it('makeEvent 产出的事件可被重新解析', () => {
    const event = makeEvent('capture.stop', {}, { ts: 1763700000000 })
    expect(event.v).toBe(PROTOCOL_VERSION)
    const parsed = classifyIncoming(JSON.parse(JSON.stringify(event)) as unknown)
    expect(parsed.kind).toBe('ok')
    if (parsed.kind === 'ok') {
      expect(parsed.event).toEqual(event as EventV1)
    }
  })

  it('带 requestId 的事件保留关联字段', () => {
    const event = makeEvent('ping', { nonce: 'x' }, { requestId: newRequestId() })
    expect(event.requestId).toMatch(/^req_/)
  })
})
