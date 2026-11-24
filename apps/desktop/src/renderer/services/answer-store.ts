/**
 * P52-05：答案事件消费。
 *
 * 纯状态机：不依赖 DOM/WebSocket，便于测试；按 session/request/generation/sequence
 * 去重，终态后拒绝继续 delta。
 */

export type AnswerState = 'idle' | 'running' | 'completed' | 'cancelled' | 'failed'

export interface AnswerEventLike {
  type: string
  sessionId: string
  requestId: string
  questionId: string
  generation: number
  sequence: number
  payload: unknown
}

export interface AnswerRequestState {
  requestId: string
  questionId: string
  generation: number
  state: AnswerState
  question: string
  text: string
  sequence: number
}

export interface AnswerSessionState {
  sessionId: string
  state: AnswerState
  generation: number
  currentRequestId: string | null
  requests: Map<string, AnswerRequestState>
  recent: string[]
}

const TERMINAL = new Set(['answer.completed', 'answer.cancelled', 'answer.failed'])

export class AnswerStore {
  private readonly sessions = new Map<string, AnswerSessionState>()

  session(sessionId: string): AnswerSessionState {
    const existing = this.sessions.get(sessionId)
    if (existing) return existing
    const created: AnswerSessionState = {
      sessionId,
      state: 'idle',
      generation: -1,
      currentRequestId: null,
      requests: new Map(),
      recent: [],
    }
    this.sessions.set(sessionId, created)
    return created
  }

  apply(event: AnswerEventLike): boolean {
    const session = this.session(event.sessionId)
    if (event.generation < session.generation) return false
    if (event.generation > session.generation) {
      session.generation = event.generation
      session.currentRequestId = null
      session.state = 'running'
    }
    let request = session.requests.get(event.requestId)
    if (request && (request.state === 'completed' || request.state === 'cancelled' || request.state === 'failed')) {
      return false
    }
    if (request && event.sequence <= request.sequence) return false

    if (!request) {
      request = {
        requestId: event.requestId,
        questionId: event.questionId,
        generation: event.generation,
        state: 'running',
        question: '',
        text: '',
        sequence: 0,
      }
      session.requests.set(event.requestId, request)
    }
    request.sequence = event.sequence
    request.questionId = event.questionId
    session.currentRequestId = event.requestId
    session.state = 'running'

    const payload = (event.payload ?? {}) as Record<string, unknown>
    if (event.type === 'answer.started') {
      request.question = String(payload.question ?? '')
      return true
    }
    if (event.type === 'answer.delta') {
      request.text += String(payload.delta ?? '')
      return true
    }
    if (event.type === 'answer.completed') {
      request.text = String(payload.answer ?? request.text)
      this.finish(session, request, 'completed')
      return true
    }
    if (event.type === 'answer.cancelled') {
      this.finish(session, request, 'cancelled')
      return true
    }
    if (event.type === 'answer.failed') {
      this.finish(session, request, 'failed')
      return true
    }
    return true
  }

  private finish(session: AnswerSessionState, request: AnswerRequestState, state: AnswerState): void {
    request.state = state
    session.state = state
    session.recent = [request.requestId, ...session.recent.filter((id) => id !== request.requestId)].slice(0, 10)
  }
}

export function isTerminal(type: string): boolean {
  return TERMINAL.has(type)
}
