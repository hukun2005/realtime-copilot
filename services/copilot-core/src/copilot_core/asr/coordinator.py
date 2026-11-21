"""ASR 会话协调器：把 AsrManager 的生命周期绑定到采集会话 generation。

- Core 启动时 ASR 保持 IDLE，不创建 Manager。
- capture.start 后调用 ``start(generation)``：创建并连接 Manager。
- capture.stop / 失败收尾调用 ``stop()``：取消识别、关闭 Provider、提升 generation 使旧结果失效。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .manager import AsrManager
from .provider import AsrProvider


class AsrCoordinator:
    """按采集代次管理唯一的 AsrManager。"""

    def __init__(
        self,
        *,
        pipeline: Any,
        tickets: Any,
        provider_factory: Callable[[], AsrProvider],
        broadcast: Callable[[Any], Awaitable[None]],
        logger: Any = None,
        segment_timeout_seconds: float = 15.0,
    ) -> None:
        self._pipeline = pipeline
        self._tickets = tickets
        self._provider_factory = provider_factory
        self._broadcast = broadcast
        self._logger = logger
        self._segment_timeout = segment_timeout_seconds
        self._manager: AsrManager | None = None
        self._generation = 0
        self._lock = asyncio.Lock()

    @property
    def manager(self) -> AsrManager | None:
        return self._manager

    @property
    def generation(self) -> int:
        return self._generation

    async def start(self, generation: int) -> None:
        """为新的采集会话创建 Manager；失败不影响采集。"""
        async with self._lock:
            await self._stop_locked()
            self._generation = generation
            try:
                provider = self._provider_factory()
            except Exception:
                if self._logger is not None:
                    self._logger.exception("asr provider creation failed")
                self._generation = 0
                return

            manager = AsrManager(
                pipeline=self._pipeline,
                tickets=self._tickets,
                provider=provider,
                generation=generation,
                broadcast=self._broadcast,
                segment_timeout_seconds=self._segment_timeout,
                current_generation=lambda: self._generation,
                logger=self._logger,
            )
            self._manager = manager
            started = await manager.start()
            if not started and self._logger is not None:
                self._logger.warning("asr manager failed to start for generation %s", generation)

    async def stop(self) -> None:
        """停止当前 Manager 并拒绝旧 generation 的迟到结果；幂等。"""
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        # 先让 generation 失效，再停止，保证迟到结果被拒绝。
        self._generation = 0
        manager = self._manager
        self._manager = None
        if manager is None:
            return
        try:
            await manager.stop()
        except Exception:
            if self._logger is not None:
                self._logger.exception("asr manager stop failed")
