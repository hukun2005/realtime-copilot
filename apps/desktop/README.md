# 桌面端（Electron + Vue 3）

Phase 1 的桌面骨架：连接 Core、展示连接状态、暴露最小 IPC 面。**不是悬浮窗**，悬浮窗交互在 Phase 6。

## 结构

```text
src/
├── main/           # 主进程（CommonJS，tsc 输出到 dist/main）
│   ├── main.ts             # 窗口、IPC、smoke 模式
│   ├── core-connection.ts  # ws 客户端：hello、心跳、断线
│   ├── core-state.ts       # 纯函数状态机（可单测，不依赖 Electron）
│   └── runtime-file.ts     # 读 runtime.json
├── preload/        # contextBridge，只暴露 4 个方法
├── renderer/       # Vue 3 + Vite
└── shared/         # 主进程 / preload / 渲染进程共享的类型
```

## 开发

```bash
pnpm -C apps/desktop build     # 先构建 protocol（prebuild 自动执行）
pnpm -C apps/desktop start     # 需要 Core 已在运行
pnpm -C apps/desktop typecheck # tsc（主进程）+ vue-tsc（渲染进程）
pnpm -C apps/desktop test      # vitest：状态机与 runtime.json 解析
```

## 状态里的"音频进程"

主进程读取 Core 的 `/healthz` 的 `roles` 字段，`roles.audio > 0` 表示音频进程已连接。这是真实跨进程状态，不是模拟值。

## Smoke 模式

```bash
# Core 运行时；ELECTRON_RUN_AS_NODE 必须未设置
pnpm -C apps/desktop exec electron . --smoke
```

启动隐藏窗口，等待渲染进程加载完成且 Core 进入 `connected`，再从页面读取文本确认 Vue 已挂载（包含"Core 服务"与"已连接"），向 stdout 打印一行 JSON 后退出（0 成功 / 1 超时或未渲染）。CI 不运行 Electron，仅本地验证。

## 页面来源与 CSP

渲染进程通过自定义协议 `copilot://app/` 加载（`protocol.handle` 映射到 `dist/renderer`），而不是 `file://`。原因：`file://` 没有可用的 origin，`script-src 'self'` 会直接拦掉打包后的模块脚本；自定义标准协议让 CSP 真正生效，并且能对越界路径返回 403。

`index.html` 携带内容安全策略：`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; ...`，Electron 的 Insecure-CSP 警告因此消失。

## 安全

`contextIsolation` + `sandbox` 开启，`nodeIntegration` 关闭；preload 没有通用 `send`/`invoke`。见 [SECURITY.md](../../SECURITY.md)。
