import type { AudioStatus, CoreStatus, DesktopSnapshot } from './status'

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
}
