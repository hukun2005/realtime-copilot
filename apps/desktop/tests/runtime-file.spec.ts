import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { readRuntimeFile, resolveRuntimePath } from '../src/main/runtime-file'

const created: string[] = []

function tempDir(): string {
  const directory = mkdtempSync(join(tmpdir(), 'rtc-desktop-'))
  created.push(directory)
  return directory
}

afterEach(() => {
  for (const directory of created.splice(0)) {
    rmSync(directory, { recursive: true, force: true })
  }
})

describe('resolveRuntimePath', () => {
  it('prefers an explicit file', () => {
    expect(resolveRuntimePath({ RTC_RUNTIME_FILE: '/tmp/x.json' })).toBe('/tmp/x.json')
  })

  it('falls back to the data directory', () => {
    const path = resolveRuntimePath({ RTC_DATA_DIR: '/tmp/data' })
    expect(path.endsWith('runtime.json')).toBe(true)
    expect(path).toContain('data')
  })
})

describe('readRuntimeFile', () => {
  it('returns null when the file is missing', () => {
    expect(readRuntimeFile(join(tempDir(), 'absent.json'))).toBeNull()
  })

  it('returns null for malformed JSON', () => {
    const path = join(tempDir(), 'runtime.json')
    writeFileSync(path, '{not json')
    expect(readRuntimeFile(path)).toBeNull()
  })

  it('returns null when required fields are missing', () => {
    const path = join(tempDir(), 'runtime.json')
    writeFileSync(path, JSON.stringify({ port: 1234 }))
    expect(readRuntimeFile(path)).toBeNull()
  })

  it('parses a core runtime file', () => {
    const path = join(tempDir(), 'runtime.json')
    writeFileSync(
      path,
      JSON.stringify({
        version: '0.1.0',
        protocol: 1,
        pid: 42,
        host: '127.0.0.1',
        port: 45678,
        wsUrl: 'ws://127.0.0.1:45678/ws/v1/events',
        token: 'a'.repeat(64),
        mode: 'standalone',
      }),
    )

    const runtime = readRuntimeFile(path)
    expect(runtime).not.toBeNull()
    expect(runtime?.port).toBe(45678)
    expect(runtime?.wsUrl).toBe('ws://127.0.0.1:45678/ws/v1/events')
    expect(runtime?.mode).toBe('standalone')
    expect(runtime?.path).toBe(path)
  })
})
