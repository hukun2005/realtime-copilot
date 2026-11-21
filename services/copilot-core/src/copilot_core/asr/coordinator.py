"""ASR 会话协调器：把 AsrManager 生命周期绑定到采集会话 generation。

并发语义：

- ``start(generation)`` 立即登记 generation 与 epoch，网络连接在锁外进行；
  完成后若 epoch/generation 已变化（期间发生过 stop），立即关闭刚创建的 Manager。
- ``stop()`` 先递增 epoch、使 generation 失效，再在锁外关闭 Manager；
  因此 stop 不会等待正在进行的连接，也不会被旧 start 复活。
- Core shutdown 调用 ``shutdown()``：禁止新 start，等待所有生命周期任务结束。
"""

from __future__ import annotations

import asyncio
import contextlib
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
        self._epoch = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self._lifecycle: set[asyncio.Task[None]] = set()

    @property
    def manager(self) -> AsrManager | None:
        return self._manager

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def active_tasks(self) -> int:
        return len(self._lifecycle)

    def snapshot(self) -> dict[str, Any]:
        """``/healthz.asr``：状态 + 生命周期任务数 + Provider 指标。"""
        manager = self._manager
        data: dict[str, Any] = (
            manager.snapshot()
            if manager is not None
            else {"state": "idle", "generation": self._generation}
        )
        data["activeTasks"] = self.active_tasks
        provider = getattr(manager, "_provider", None) if manager is not None else None
        metrics = getattr(provider, "metrics", None)
        if callable(metrics):
            data["metrics"] = metrics()
        return data

    async def start(self, generation: int) -> None:
        """为采集会话创建 Manager；网络连接在锁外，失败不影响采集。"""
        if self._closed:
            return

        epoch: int
        manager: AsrManager
        async with self._lock:
            await self._stop_locked()
            self._epoch += 1
            epoch = self._epoch
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

        task = asyncio.create_task(self._run_start(manager, generation, epoch))
        self._track(task)
        # 允许调用方等待本次连接完成；并发 stop 会通过 epoch 使其失效。
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run_start(self, manager: AsrManager, generation: int, epoch: int) -> None:
        try:
            started = await manager.start()
        except Exception:
            if self._logger is not None:
                self._logger.exception("asr manager start failed")
            started = False

        stale = False
        async with self._lock:
            if epoch != self._epoch or self._generation != generation or self._closed:
                stale = True
                if self._manager is manager:
                    self._manager = None
        if stale or not started:
            await manager.stop()

    async def stop(self) -> None:
        """使当前 generation 失效并关闭 Manager；幂等。"""
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        self._epoch += 1
        self._generation = 0
        manager = self._manager
        self._manager = None
        if manager is not None:
            try:
                await manager.stop()
            except Exception:
                if self._logger is not None:
                    self._logger.exception("asr manager stop failed")

    async def shutdown(self) -> None:
        """Core 关闭：禁止新 start，取消并等待全部生命周期任务。"""
        async with self._lock:
            self._closed = True
        await self.stop()
        tasks = list(self._lifecycle)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                if self._logger is not None:
                    self._logger.exception("asr lifecycle task failed")
        self._lifecycle.clear()
        if self._manager is not None:
            await self.stop()

    def _track(self, task: asyncio.Task[None]) -> None:
        self._lifecycle.add(task)
        task.add_done_callback(self._lifecycle.discard)
