import { describe, expect, it } from 'vitest'
import {
  AnswerEventGate,
  nextReconnectDelay,
  type AnswerEvent,
} from '../src/main/answer-events'

function event(overrides: Partial<AnswerEvent> = {}): AnswerEvent {
  return {
    v: 1,
    type: 'answer.delta',
    eventId: 'evt_1',
    requestId: 'req_1',
    sessionId: 'sess_1',
    questionId: 'q_1',
    generation: 1,
    sequence: 1,
    ts: 1,
    payload: { delta: 'A' },
    ...overrides,
  } as AnswerEvent
}

describe('AnswerEventGate', () => {
  it('accepts ordered events and drops duplicates', () => {
    const gate = new AnswerEventGate()
    expect(gate.accept(event({ type: 'answer.started', sequence: 1, payload: {} as never }))).toBe(true)
    expect(gate.accept(event({ sequence: 2 }))).toBe(true)
    expect(gate.accept(event({ sequence: 2 }))).toBe(false)
    expect(gate.accept(event({ sequence: 1 }))).toBe(false)
  })

  it('drops old generation and post-terminal deltas', () => {
    const gate = new AnswerEventGate()
    expect(gate.accept(event({ type: 'answer.completed', generation: 2, sequence: 1, payload: { answer: 'x' } as never }))).toBe(true)
    expect(gate.accept(event({ generation: 1, sequence: 9 }))).toBe(false)
    expect(gate.accept(event({ generation: 2, sequence: 2 }))).toBe(false)
  })

  it('allows a new request in the same session', () => {
    const gate = new AnswerEventGate()
    expect(gate.accept(event({ type: 'answer.completed', requestId: 'req_1', sequence: 1, payload: {} as never }))).toBe(true)
    expect(gate.accept(event({ type: 'answer.started', requestId: 'req_2', sequence: 1, payload: {} as never }))).toBe(true)
  })
})

describe('nextReconnectDelay', () => {
  it('is bounded', () => {
    expect(nextReconnectDelay(0)).toBe(1000)
    expect(nextReconnectDelay(4)).toBe(30000)
    expect(nextReconnectDelay(99)).toBe(30000)
  })
})
