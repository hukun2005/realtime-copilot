<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { DesktopSnapshot } from '../shared/status'
import type { ResumeProjectRecord, ResumeRecord } from '../shared/bridge'

const snapshot = ref<DesktopSnapshot | null>(null)
const error = ref<string | null>(null)
type View = 'dashboard' | 'resume' | 'github' | 'analysis' | 'knowledge' | 'settings'
const activeView = ref<View>('dashboard')
const views: { id: View; label: string }[] = [
  { id: 'dashboard', label: '总览' },
  { id: 'resume', label: '简历' },
  { id: 'github', label: 'GitHub' },
  { id: 'analysis', label: '代码分析' },
  { id: 'knowledge', label: '知识库' },
  { id: 'settings', label: '设置' },
]
const resumes = ref<ResumeRecord[]>([])
const resumeError = ref<string | null>(null)
const uploading = ref(false)
const edits = ref<Record<string, { tech: string; github: string }>>({})
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


async function loadResumes(): Promise<void> {
  try {
    resumes.value = await window.copilot.resumes()
    for (const resume of resumes.value) {
      for (const project of resume.projects) {
        edits.value[project.id] = {
          tech: project.tech_stack.join(', '),
          github: project.github_urls.join(', '),
        }
      }
    }
    resumeError.value = null
  } catch (cause) {
    resumeError.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function onResumeFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  resumeError.value = null
  try {
    await window.copilot.uploadResume(file.name, await file.arrayBuffer())
    await loadResumes()
  } catch (cause) {
    resumeError.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function activateResume(id: string): Promise<void> {
  try {
    await window.copilot.activateResume(id)
  } catch (cause) {
    resumeError.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function deleteResume(id: string): Promise<void> {
  try {
    await window.copilot.deleteResume(id)
    await loadResumes()
  } catch (cause) {
    resumeError.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function saveProject(project: ResumeProjectRecord): Promise<void> {
  const edit = edits.value[project.id]
  if (!edit) return
  try {
    await window.copilot.updateResumeProject(project.id, {
      tech_stack: edit.tech.split(',').map((item) => item.trim()).filter(Boolean),
      github_urls: edit.github.split(',').map((item) => item.trim()).filter(Boolean),
    })
    await loadResumes()
  } catch (cause) {
    resumeError.value = cause instanceof Error ? cause.message : String(cause)
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
  await loadResumes()
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

    <nav class="tabs">
      <button v-for="view in views" :key="view.id" type="button" :class="{ active: activeView === view.id }" @click="activeView = view.id">
        {{ view.label }}
      </button>
    </nav>

    <section v-if="activeView === 'dashboard'" class="cards">
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

    <section v-if="activeView === 'resume'" class="resumes">
      <div class="resume-head">
        <h2>简历管理</h2>
        <label class="upload">
          {{ uploading ? '解析中…' : '上传 PDF / DOCX' }}
          <input type="file" accept=".pdf,.docx" :disabled="uploading" @change="onResumeFile" />
        </label>
      </div>
      <p v-if="resumeError" class="resume-error">{{ resumeError }}</p>
      <p v-if="resumes.length === 0" class="empty">还没有简历。上传后会自动提取项目。</p>
      <article v-for="resume in resumes" :key="resume.id" class="resume-card">
        <header>
          <strong>{{ resume.name }}</strong>
          <span class="muted">{{ resume.file_name }} · {{ resume.status }}</span>
          <div class="resume-actions">
            <button type="button" @click="activateResume(resume.id)">设为当前</button>
            <button type="button" class="danger" @click="deleteResume(resume.id)">删除</button>
          </div>
        </header>
        <div v-for="project in resume.projects" :key="project.id" class="project">
          <p class="project-name">{{ project.project_name }}</p>
          <p class="muted">{{ project.source_location }} · 置信度 {{ project.confidence }}</p>
          <label>技术栈 <input v-model="edits[project.id]!.tech" /></label>
          <label>GitHub <input v-model="edits[project.id]!.github" /></label>
          <button type="button" @click="saveProject(project)">保存项目</button>
        </div>
      </article>
    </section>

    <section v-if="activeView !== 'dashboard'" class="placeholder">
      <h2>{{ views.find((item) => item.id === activeView)?.label }}</h2>
      <p v-if="activeView === 'github'">GitHub 仓库关联页面将在后续批次接入；当前后端核心已完成。</p>
      <p v-else-if="activeView === 'analysis'">代码分析页面将在后续批次接入；当前后端已完成 Tree/静态分析。</p>
      <p v-else-if="activeView === 'knowledge'">知识库检索页面将在后续批次接入；当前 FTS5 已完成。</p>
      <p v-else>面试设置与悬浮窗将在后续批次接入。</p>
    </section>

    <section v-if="activeView === 'dashboard' && problems.length > 0" class="problems">
      <h2>需要处理</h2>
      <ul>
        <li v-for="problem in problems" :key="problem">{{ problem }}</li>
      </ul>
    </section>

    <section v-if="activeView === 'dashboard'" class="facts">
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
