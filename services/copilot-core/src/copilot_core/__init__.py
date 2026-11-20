"""RealTime Copilot 核心服务。

模块划分：

- :mod:`copilot_core.config` 配置与校验
- :mod:`copilot_core.app` FastAPI 装配（``/healthz``、``/ws/v1/events``）
- :mod:`copilot_core.runtime` 监听与运行
- :mod:`copilot_core.ws` 连接生命周期（鉴权、心跳、分发）
- :mod:`copilot_core.storage` SQLite 与迁移
- :mod:`copilot_core.security` 令牌与运行时文件
"""

from .version import __version__

__all__ = ["__version__"]
