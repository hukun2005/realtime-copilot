import type { AudioStatus, CoreStatus, DesktopSnapshot } from './status'

export interface ResumeProjectRecord {
  id: string
  resume_id: string
  project_name: string
  description: string
  role: string
  tech_stack: string[]
  start_date: string | null
  end_date: string | null
  achievements: string[]
  github_urls: string[]
  confidence: number
  source_location: string
}

export interface ResumeRecord {
  id: string
  name: string
  file_name: string
  status: string
  projects: ResumeProjectRecord[]
}

export interface ResumeProjectPatch {
  project_name?: string
  description?: string
  role?: string
  tech_stack?: string[]
  github_urls?: string[]
}

/** preload 通过 contextBridge 暴露的唯一接口；没有通用 invoke/send。 */
export interface CopilotBridge {
  /** 读取一次完整快照（Core 状态 + 运行时信息 + 音频状态）。 */
  snapshot(): Promise<DesktopSnapshot>
  /** 单独读取音频连接状态（Core 的 /healthz）。 */
  audio(): Promise<AudioStatus>
  /** 重新读取 runtime.json 并重连。 */
  reconnect(): Promise<DesktopSnapshot>
  /** 订阅 Core 状态变化；返回取消订阅函数。 */
  onCoreStatus(listener: (status: CoreStatus) => void): () => void
  /** 简历列表；不含 Token。 */
  resumes(): Promise<ResumeRecord[]>
  /** 上传 PDF/DOCX；只传文件名与字节，不传路径。 */
  uploadResume(fileName: string, data: ArrayBuffer): Promise<ResumeRecord>
  /** 设为当前简历。 */
  activateResume(id: string): Promise<void>
  /** 删除简历及其项目。 */
  deleteResume(id: string): Promise<void>
  /** 编辑项目字段。 */
  updateResumeProject(id: string, patch: ResumeProjectPatch): Promise<ResumeProjectRecord>
}
