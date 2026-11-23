import { describe, expect, it } from 'vitest'
import { AnswerStore, type AnswerEventLike } from '../src/renderer/services/answer-store'

function event(overrides: Partial<AnswerEventLike> = {}): AnswerEventLike {
  return {
    type: 'answer.delta',
    sessionId: 'sess_1',
    requestId: 'req_1',
    questionId: 'q_1',
    generation: 1,
    sequence: 1,
    payload: { delta: 'A' },
    ...overrides,
  }
}

describe('AnswerStore', () => {
  it('appends deltas and completes', () => {
    const store = new AnswerStore()
    expect(store.apply(event({ type: 'answer.started', payload: {} }))).toBe(true)
    expect(store.apply(event({ sequence: 2, payload: { delta: 'A' } }))).toBe(true)
    expect(store.apply(event({ sequence: 3, payload: { delta: 'B' } }))).toBe(true)
    expect(store.apply(event({ type: 'answer.completed', sequence: 4, payload: { answer: 'AB' } }))).toBe(true)
    const session = store.session('sess_1')
    expect(session.state).toBe('completed')
    expect(session.requests.get('req_1')?.text).toBe('AB')
  })

  it('drops duplicate and out-of-order sequences', () => {
    const store = new AnswerStore()
    expect(store.apply(event({ sequence: 2, payload: { delta: 'A' } }))).toBe(true)
    expect(store.apply(event({ sequence: 2, payload: { delta: 'dup' } }))).toBe(false)
    expect(store.apply(event({ sequence: 1, payload: { delta: 'old' } }))).toBe(false)
    expect(store.session('sess_1').requests.get('req_1')?.text).toBe('A')
  })

  it('ignores old generation and terminal deltas', () => {
    const store = new AnswerStore()
    store.apply(event({ type: 'answer.completed', generation: 2, sequence: 1, payload: { answer: 'new' } }))
    expect(store.apply(event({ generation: 1, sequence: 9, payload: { delta: 'old' } }))).toBe(false)
    expect(store.apply(event({ generation: 2, sequence: 2, payload: { delta: 'late' } }))).toBe(false)
  })

  it('keeps sessions isolated', () => {
    const store = new AnswerStore()
    store.apply(event({ sessionId: 's1', payload: { delta: 'one' } }))
    store.apply(event({ sessionId: 's2', requestId: 'req_2', payload: { delta: 'two' } }))
    expect(store.session('s1').requests.get('req_1')?.text).toBe('one')
    expect(store.session('s2').requests.get('req_2')?.text).toBe('two')
  })
})
