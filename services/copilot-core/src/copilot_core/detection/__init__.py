"""P4 问题检测。"""

from .answer import AnswerProvider, AnswerResult, UnavailableAnswerProvider, answer_question
from .classifier import Detection, QuestionType, detect_question
from .engine import QuestionEngine
from .followup import FollowUpResolver
from .llm import DeepSeekAnswerProvider, DeepSeekClient, DeepSeekReviewer, LlmNotConfigured, parse_review
from .review import NoopReviewer, QuestionReviewer, ReviewDecision, apply_review
from .state import (
    InvalidTransition,
    Question,
    QuestionState,
    QuestionStore,
)
from .transcript import TranscriptEntry, TranscriptLog

__all__ = [
    "AnswerProvider",
    "AnswerResult",
    "DeepSeekAnswerProvider",
    "DeepSeekClient",
    "DeepSeekReviewer",
    "Detection",
    "FollowUpResolver",
    "InvalidTransition",
    "LlmNotConfigured",
    "NoopReviewer",
    "Question",
    "QuestionEngine",
    "QuestionReviewer",
    "QuestionState",
    "QuestionStore",
    "QuestionType",
    "ReviewDecision",
    "TranscriptEntry",
    "TranscriptLog",
    "UnavailableAnswerProvider",
    "answer_question",
    "apply_review",
    "detect_question",
    "parse_review",
]
