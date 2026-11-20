<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { DesktopSnapshot } from '../shared/status'

const snapshot = ref<DesktopSnapshot | null>(null)
const error = ref<string | null>(null)
let refreshTimer: number | undefined
let unsubscribe: (() => void) | undefined

const coreLabel = computed(() => {
  const phase = snapshot.value?.core.phase ?? 'idle'
  return {
    idle: '未连接',
    connecting: '连接中',
    connected: '已连接',
    closed: '已断开',
    error: '错误',
  }[phase]
})

const coreTone = computed(() => {
  const phase = snapshot.value?.core.phase ?? 'idle'
  if (phase === 'connected') return 'ok'
  if (phase === 'connecting') return 'warn'
  if (phase === 'idle') return 'muted'
  return 'bad'
})

const audioLabel = computed(() => {
  const audio = snapshot.value?.audio
  if (!audio || !audio.connected) return '不可用'
  return (audio.roles.audio ?? 0) > 0 ? '已连接' : '未连接'
})

const audioTone = computed(() => {
  const audio = snapshot.value?.audio
  if (!audio || !audio.connected) return 'muted'
  return (audio.roles.audio ?? 0) > 0 ? 'ok' : 'warn'
})

const modeLabel = computed(() => snapshot.value?.runtime.mode ?? '未知')

const problems = computed(() => {
  const items: string[] = []
  if (error.value) items.push(error.value)
  if (!snapshot.value) return items
  if (snapshot.value.core.error) items.push(snapshot.value.core.error)
  if (!snapshot.value.runtime.found) items.push(`未找到 runtime.json：${snapshot.value.runtime.path}`)
  if (snapshot.value.audio.error && snapshot.value.audio.connected === false && snapshot.value.core.phase === 'connected') {
    items.push(`音频状态不可读：${snapshot.value.audio.error}`)
  }
  return items
})

async function refresh(): Promise<void> {
  try {
    snapshot.value = await window.copilot.snapshot()
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function reconnect(): Promise<void> {
  try {
    snapshot.value = await window.copilot.reconnect()
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

onMounted(async () => {
  unsubscribe = window.copilot.onCoreStatus((status) => {
    if (snapshot.value) {
      snapshot.value.core = status
    }
  })
  await refresh()
  refreshTimer = window.setInterval(() => {
    void refresh()
  }, 2000)
})

onUnmounted(() => {
  if (refreshTimer !== undefined) {
    window.clearInterval(refreshTimer)
  }
  unsubscribe?.()
})
</script>

<template>
  <main class="shell">
    <header class="header">
      <div>
        <h1>RealTime Copilot</h1>
        <p class="subtitle">Phase 1 骨架：通信与状态可见，尚未采集音频</p>
      </div>
      <div class="actions">
        <span class="mode">模式：{{ modeLabel }}</span>
        <button type="button" @click="reconnect">重连</button>
      </div>
    </header>

    <section class="cards">
      <article class="card" :data-tone="coreTone">
        <h2>Core 服务</h2>
        <p class="value">{{ coreLabel }}</p>
        <dl>
          <dt>地址</dt>
          <dd>{{ snapshot?.core.url ?? '—' }}</dd>
          <dt>版本</dt>
          <dd>{{ snapshot?.core.serverVersion ?? '—' }}</dd>
          <dt>协议</dt>
          <dd>{{ snapshot?.core.protocol ?? '—' }}</dd>
        </dl>
      </article>

      <article class="card" :data-tone="audioTone">
        <h2>音频进程</h2>
        <p class="value">{{ audioLabel }}</p>
        <dl>
          <dt>角色连接</dt>
          <dd>{{ Object.keys(snapshot?.audio.roles ?? {}).length === 0 ? '—' : JSON.stringify(snapshot?.audio.roles) }}</dd>
          <dt>状态来源</dt>
          <dd>Core /healthz</dd>
        </dl>
      </article>
    </section>

    <section v-if="problems.length > 0" class="problems">
      <h2>需要处理</h2>
      <ul>
        <li v-for="problem in problems" :key="problem">{{ problem }}</li>
      </ul>
    </section>

    <section class="facts">
      <h2>运行时</h2>
      <dl>
        <dt>应用版本</dt>
        <dd>{{ snapshot?.appVersion ?? '—' }}</dd>
        <dt>runtime.json</dt>
        <dd>{{ snapshot?.runtime.path ?? '—' }}</dd>
        <dt>Core 端口</dt>
        <dd>{{ snapshot?.runtime.port ?? '—' }}</dd>
        <dt>Core 版本</dt>
        <dd>{{ snapshot?.runtime.version ?? '—' }}</dd>
      </dl>
    </section>

    <footer class="footer">
      本阶段只有协议、鉴权、心跳与状态展示；音频采集、ASR、问答与悬浮窗在后续阶段实现。
    </footer>
  </main>
</template>
