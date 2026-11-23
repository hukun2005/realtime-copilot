"""P51-01：答案运行时，负责任务、generation、取消与事件。"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from ..detection import AnswerContext, Detection, Question
from .events import RuntimeEvent
from .models import AnswerTask, TaskState
from .review import ReviewService


class StreamProvider(Protocol):
    def stream(self, *, question: Question, context: AnswerContext) -> AsyncIterator[str]: ...


class EventPublisher(Protocol):
    async def publish(self, event: RuntimeEvent) -> None: ...


ContextFactory = Callable[[Question, str | None], Awaitable[AnswerContext]]


class AnswerRuntime:
    def __init__(
        self,
        *,
        provider: StreamProvider,
        publish: Callable[[RuntimeEvent], Awaitable[None]],
        context_factory: ContextFactory | None = None,
        reviewer: ReviewService | None = None,
    ) -> None:
        self._provider = provider
        self._publish = publish
        self._context_factory = context_factory or _default_context
        self._reviewer = reviewer
        self._tasks: dict[str, AnswerTask] = {}
        self._handles: dict[str, asyncio.Task[None]] = {}
        self._session_active: dict[str, str] = {}
        self._generations: dict[str, int] = {}
        self._cancel_reasons: dict[str, str] = {}
        self._question_requests: dict[tuple[str, str], str] = {}
        self.publish_errors = 0

    async def submit_question(
        self,
        question: Question,
        *,
        session_id: str,
        project_id: str | None = None,
    ) -> str:
        existing = self._question_requests.get((session_id, question.id))
        if existing is not None and existing in self._tasks and self._tasks[existing].state not in (
            TaskState.COMPLETED,
            TaskState.CANCELLED,
            TaskState.FAILED,
        ):
            return existing

        active = self._session_active.get(session_id)
        if active is not None:
            await self.cancel(active, reason="superseded")

        generation = self._generations.get(session_id, 0) + 1
        self._generations[session_id] = generation
        request_id = "req_" + uuid.uuid4().hex
        task = AnswerTask(
            request_id=request_id,
            session_id=session_id,
            question=question,
            generation=generation,
        )
        self._tasks[request_id] = task
        self._session_active[session_id] = request_id
        self._question_requests[(session_id, question.id)] = request_id
        task.state = TaskState.RUNNING
        task.started_at = asyncio.get_running_loop().time()
        self._handles[request_id] = asyncio.create_task(
            self._execute(task, project_id=project_id)
        )
        return request_id

    async def _execute(self, task: AnswerTask, *, project_id: str | None) -> None:
        try:
            await self._emit(task, "answer.started", {"question": task.question.text, "status": "running"})
            if self._reviewer is not None:
                detection = Detection(
                    is_question=True,
                    question_type=task.question.question_type,
                    confidence=task.question.confidence,
                    text=task.question.text,
                    normalized_text=task.question.normalized_text,
                    reason="runtime",
                    source=task.question.source,
                    segment_id=task.question.segment_id,
                )
                outcome = await self._reviewer.review(question=task.question, detection=detection)
                if not self._reviewer.should_answer(outcome, detection):
                    task.finish(TaskState.CANCELLED)
                    await self._emit(task, "answer.cancelled", {"reason": "review_rejected"})
                    return
            context = await self._context_factory(task.question, project_id)
            async for chunk in self._provider.stream(question=task.question, context=context):
                if not self._is_current(task):
                    return
                if not chunk:
                    continue
                task.text += chunk
                await self._emit(task, "answer.delta", {"delta": chunk})
            if not self._is_current(task):
                return
            task.finish(TaskState.COMPLETED)
            await self._emit(task, "answer.completed", {"answer": task.text, "finishReason": "stop"})
        except asyncio.CancelledError:
            if task.state not in (TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED):
                task.finish(TaskState.CANCELLED)
                await self._emit(
                    task,
                    "answer.cancelled",
                    {"reason": self._cancel_reasons.get(task.request_id, "user_cancelled")},
                )
            raise
        except Exception as error:
            if task.state not in (TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED):
                task.error = type(error).__name__
                task.finish(TaskState.FAILED)
                await self._emit(
                    task,
                    "answer.failed",
                    {"code": task.error, "message": "answer generation failed", "retryable": False},
                )
        finally:
            self._handles.pop(task.request_id, None)
            if self._session_active.get(task.session_id) == task.request_id:
                self._session_active.pop(task.session_id, None)

    async def cancel(self, request_id: str, reason: str = "user_cancelled") -> bool:
        task = self._tasks.get(request_id)
        if task is None or task.state in (TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED):
            return False
        self._cancel_reasons[request_id] = reason
        handle = self._handles.get(request_id)
        if handle is not None and not handle.done():
            handle.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await handle
        if task.state not in (TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED):
            task.finish(TaskState.CANCELLED)
            await self._emit(task, "answer.cancelled", {"reason": reason})
        return True

    async def cancel_session(self, session_id: str) -> int:
        active = [
            task.request_id
            for task in self._tasks.values()
            if task.session_id == session_id
            and task.state not in (TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED)
        ]
        for request_id in active:
            await self.cancel(request_id, reason="session_stopped")
        return len(active)

    def status(self, request_id: str) -> dict[str, object] | None:
        task = self._tasks.get(request_id)
        if task is None:
            return None
        return {
            "requestId": task.request_id,
            "sessionId": task.session_id,
            "questionId": task.question.id,
            "generation": task.generation,
            "state": task.state.value,
            "sequence": task.sequence,
            "textLength": len(task.text),
            "error": task.error,
        }

    def cleanup(self, *, keep: int = 100) -> int:
        terminal = [task for task in self._tasks.values() if task.state in (
            TaskState.COMPLETED,
            TaskState.CANCELLED,
            TaskState.FAILED,
        )]
        terminal.sort(key=lambda task: task.created_at)
        remove = terminal[: max(0, len(terminal) - keep)]
        for task in remove:
            self._tasks.pop(task.request_id, None)
            self._cancel_reasons.pop(task.request_id, None)
            self._question_requests.pop((task.session_id, task.question.id), None)
        return len(remove)

    def _is_current(self, task: AnswerTask) -> bool:
        return self._session_active.get(task.session_id) == task.request_id and self._generations.get(
            task.session_id
        ) == task.generation

    async def _emit(self, task: AnswerTask, event_type: str, payload: dict[str, object]) -> None:
        event = RuntimeEvent(
            type=event_type,
            session_id=task.session_id,
            request_id=task.request_id,
            question_id=task.question.id,
            generation=task.generation,
            sequence=task.next_sequence(),
            payload=payload,
        )
        try:
            await self._publish(event)
        except Exception:
            self.publish_errors += 1


async def _default_context(question: Question, _project_id: str | None) -> AnswerContext:
    return AnswerContext(
        question_id=question.id,
        question_text=question.text,
        question_type=question.question_type,
    )
