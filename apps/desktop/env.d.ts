/// <reference types="vite/client" />

import type { CopilotBridge } from './src/shared/bridge'

declare global {
  interface Window {
    copilot: CopilotBridge
  }
}

export {}
