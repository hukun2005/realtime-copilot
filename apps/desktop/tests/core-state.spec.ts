import { describe, expect, it } from 'vitest'
import { INITIAL_CORE_STATUS, reduceCoreStatus } from '../src/main/core-state'

describe('reduceCoreStatus', () => {
  it('records the URL while connecting', () => {
    const status = reduceCoreStatus(INITIAL_CORE_STATUS, { kind: 'connecting', url: 'ws://127.0.0.1:1/ws' }, 100)
    expect(status.phase).toBe('connecting')
    expect(status.url).toBe('ws://127.0.0.1:1/ws')
    expect(status.updatedAt).toBe(100)
  })

  it('stores server facts on ready', () => {
    const connecting = reduceCoreStatus(INITIAL_CORE_STATUS, { kind: 'connecting', url: 'ws://x' }, 1)
    const ready = reduceCoreStatus(
      connecting,
      { kind: 'ready', serverVersion: '0.1.0', protocol: 1, mode: 'standalone' },
      2,
    )
    expect(ready.phase).toBe('connected')
    expect(ready.serverVersion).toBe('0.1.0')
    expect(ready.protocol).toBe(1)
    expect(ready.mode).toBe('standalone')
    expect(ready.error).toBeNull()
  })

  it('keeps the failure reason on close', () => {
    const ready = reduceCoreStatus(
      INITIAL_CORE_STATUS,
      { kind: 'ready', serverVersion: '0.1.0', protocol: 1, mode: 'standalone' },
      1,
    )
    const closed = reduceCoreStatus(ready, { kind: 'closed', reason: 'code 1006' }, 2)
    expect(closed.phase).toBe('closed')
    expect(closed.error).toBe('code 1006')
    expect(closed.serverVersion).toBe('0.1.0')
  })

  it('clears server facts on error', () => {
    const ready = reduceCoreStatus(
      INITIAL_CORE_STATUS,
      { kind: 'ready', serverVersion: '0.1.0', protocol: 1, mode: 'standalone' },
      1,
    )
    const failed = reduceCoreStatus(ready, { kind: 'error', message: 'auth_failed: invalid token' }, 2)
    expect(failed.phase).toBe('error')
    expect(failed.serverVersion).toBeNull()
    expect(failed.protocol).toBeNull()
    expect(failed.error).toBe('auth_failed: invalid token')
  })

  it('is pure: the input object is never mutated', () => {
    const before = { ...INITIAL_CORE_STATUS }
    reduceCoreStatus(INITIAL_CORE_STATUS, { kind: 'connecting', url: 'ws://x' }, 5)
    expect(INITIAL_CORE_STATUS).toEqual(before)
  })
})
