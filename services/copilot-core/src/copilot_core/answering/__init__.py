"""P5 实时问答运行时（answering，避免与 copilot_core.runtime 启动模块冲突）。"""

from .answer_runtime import AnswerRuntime, EventPublisher, StreamProvider
from .events import RuntimeEvent
from .ingest import FinalGuard, is_meaningful
from .models import AnswerTask, TaskState

__all__ = [
    "AnswerRuntime",
    "AnswerTask",
    "EventPublisher",
    "FinalGuard",
    "RuntimeEvent",
    "StreamProvider",
    "TaskState",
    "is_meaningful",
]
