import type { EventOf } from '@realtime-copilot/protocol'

/** CoreConnection 转发给主进程的 answer 事件联合。 */
export type AnswerEvent = EventOf<
  'answer.started' | 'answer.delta' | 'answer.completed' | 'answer.cancelled' | 'answer.failed'
>

const ANSWER_TYPES = new Set([
  'answer.started',
  'answer.delta',
  'answer.completed',
  'answer.cancelled',
  'answer.failed',
])

const TERMINAL_TYPES = new Set(['answer.completed', 'answer.cancelled', 'answer.failed'])

/** 判断事件是否属于 answer 事件。 */
export function isAnswerEvent(event: { type: string }): event is AnswerEvent {
  return ANSWER_TYPES.has(event.type)
}

/** 判断终态事件。 */
export function isTerminalAnswer(event: { type: string }): boolean {
  return TERMINAL_TYPES.has(event.type)
}

/**
 * 主进程侧的轻量门控：旧 generation、重复/乱序 sequence、终态后 delta 不转发。
 * 前端 AnswerStore 仍会做一次去重，双保险。
 */
export class AnswerEventGate {
  private readonly generations = new Map<string, number>()
  private readonly sequences = new Map<string, number>()
  private readonly terminals = new Set<string>()

  accept(event: AnswerEvent): boolean {
    const currentGeneration = this.generations.get(event.sessionId)
    if (currentGeneration !== undefined && event.generation < currentGeneration) return false
    if (currentGeneration === undefined || event.generation > currentGeneration) {
      this.generations.set(event.sessionId, event.generation)
    }
    if (this.terminals.has(event.requestId)) return false
    const last = this.sequences.get(event.requestId)
    if (last !== undefined && event.sequence <= last) return false
    this.sequences.set(event.requestId, event.sequence)
    if (isTerminalAnswer(event)) this.terminals.add(event.requestId)
    this.trim()
    return true
  }

  private trim(): void {
    while (this.sequences.size > 500) {
      const oldest = this.sequences.keys().next().value
      if (oldest === undefined) return
      this.sequences.delete(oldest)
      this.terminals.delete(oldest)
    }
  }
}

/** 有界指数退避：1/2/5/10/30 秒。 */
export function nextReconnectDelay(attempt: number): number {
  const delays = [1000, 2000, 5000, 10000, 30000]
  return delays[Math.min(Math.max(attempt, 0), delays.length - 1)] ?? 30000
}
