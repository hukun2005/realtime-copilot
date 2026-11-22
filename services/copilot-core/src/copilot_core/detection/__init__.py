"""P4 问题检测。"""

from .answer import (
    AnswerContext,
    AnswerProvider,
    AnswerResult,
    UnavailableAnswerProvider,
    answer_question,
    build_answer_context,
    build_answer_messages,
)
from .classifier import Detection, QuestionType, detect_question
from .engine import QuestionEngine
from .followup import FollowUpResolver
from .llm import (
    DeepSeekAnswerProvider,
    DeepSeekClient,
    DeepSeekReviewer,
    LlmError,
    LlmNotConfigured,
    parse_review,
)
from .review import NoopReviewer, QuestionReviewer, ReviewDecision, apply_review
from .state import (
    InvalidTransition,
    Question,
    QuestionState,
    QuestionStore,
)
from .streaming import AnswerTaskManager, StreamingAnswerProvider
from .transcript import TranscriptEntry, TranscriptLog

__all__ = [
    "AnswerContext",
    "AnswerProvider",
    "AnswerResult",
    "AnswerTaskManager",
    "DeepSeekAnswerProvider",
    "DeepSeekClient",
    "DeepSeekReviewer",
    "Detection",
    "FollowUpResolver",
    "InvalidTransition",
    "LlmError",
    "LlmNotConfigured",
    "NoopReviewer",
    "Question",
    "QuestionEngine",
    "QuestionReviewer",
    "QuestionState",
    "QuestionStore",
    "QuestionType",
    "ReviewDecision",
    "StreamingAnswerProvider",
    "TranscriptEntry",
    "TranscriptLog",
    "UnavailableAnswerProvider",
    "answer_question",
    "apply_review",
    "build_answer_context",
    "build_answer_messages",
    "detect_question",
    "parse_review",
]
