"""P5：可取消的流式回答任务管理（generation/task_id 隔离）。"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from .answer import AnswerContext
from .state import Question

DeltaHandler = Callable[[str], Awaitable[None]]
DoneHandler = Callable[[str], Awaitable[None]]
ErrorHandler = Callable[[str], Awaitable[None]]


class StreamingAnswerProvider(Protocol):
    def stream(self, *, question: Question, context: AnswerContext) -> AsyncIterator[str]: ...


class AnswerTaskManager:
    """同一时刻只允许一个回答；新任务抢占旧任务。"""

    def __init__(self) -> None:
        self._generation = 0
        self._task: asyncio.Task[None] | None = None
        self._task_id: str | None = None
        self._closed = False
        self._delta_count = 0
        self._text = ""

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(
        self,
        *,
        provider: StreamingAnswerProvider,
        question: Question,
        context: AnswerContext,
        on_delta: DeltaHandler,
        on_done: DoneHandler,
        on_error: ErrorHandler,
    ) -> str:
        if self._closed:
            raise RuntimeError("answer task manager is closed")
        await self.cancel()
        self._generation += 1
        generation = self._generation
        task_id = "ans_" + uuid.uuid4().hex
        self._task_id = task_id
        self._delta_count = 0
        self._text = ""

        async def run() -> None:
            chunks: list[str] = []
            try:
                async for chunk in provider.stream(question=question, context=context):
                    if generation != self._generation:
                        return
                    chunks.append(chunk)
                    self._delta_count += 1
                    self._text += chunk
                    await on_delta(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if generation == self._generation:
                    await on_error(type(error).__name__)
                return
            if generation == self._generation:
                await on_done("".join(chunks))

        self._task = asyncio.create_task(run())
        return task_id

    async def cancel(self) -> None:
        self._generation += 1
        task = self._task
        self._task = None
        self._task_id = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def close(self) -> None:
        if self._closed:
            return
        await self.cancel()
        self._closed = True

    def snapshot(self) -> dict[str, object]:
        return {
            "generation": self._generation,
            "taskId": self._task_id,
            "running": self.running,
            "deltaCount": self._delta_count,
            "textLength": len(self._text),
        }
