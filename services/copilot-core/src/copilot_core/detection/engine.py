"""P4 问题检测引擎：ASR Final → 检测 → 追问 → 状态。"""

from __future__ import annotations

from .classifier import Detection, QuestionType, detect_question
from .followup import FollowUpResolver
from .review import QuestionReviewer, apply_review
from .state import Question, QuestionState, QuestionStore
from .transcript import TranscriptEntry, TranscriptLog


class QuestionEngine:
    """唯一入口：partial 只更新转写，final 才进入检测。"""

    def __init__(
        self,
        *,
        generation: int = 0,
        context_timeout: float = 120.0,
        reviewer: QuestionReviewer | None = None,
    ) -> None:
        self.transcripts = TranscriptLog(generation=generation)
        self.resolver = FollowUpResolver(timeout_seconds=context_timeout)
        self.store = QuestionStore()
        self.last_error: str | None = None
        self.reviewer = reviewer

    def ingest_partial(
        self,
        *,
        segment_id: str,
        source: str,
        text: str,
        generation: int | None = None,
    ) -> TranscriptEntry | None:
        """Partial 只入转写缓存；不触发检测/回答。"""
        return self.transcripts.ingest_partial(
            segment_id=segment_id,
            source=source,
            text=text,
            generation=generation,
        )

    def ingest_final(
        self,
        *,
        segment_id: str,
        source: str,
        text: str,
        generation: int | None = None,
    ) -> tuple[Question | None, Detection | None]:
        entry = self.transcripts.ingest_final(
            segment_id=segment_id,
            source=source,
            text=text,
            generation=generation,
        )
        if entry is None:
            return None, None
        try:
            detection = detect_question(
                text=entry.text,
                source=entry.source,
                segment_id=entry.segment_id,
                has_context=self.resolver.has_context(entry.source),
            )
            if self.reviewer is not None:
                detection = apply_review(
                    detection,
                    self.reviewer.review(text=entry.text, detection=detection),
                )
            if not detection.is_question:
                return None, detection
            parent_id, reason = self.resolver.resolve(detection)
            question = self.store.add(
                detection,
                parent_id=parent_id,
                parent_reason=reason,
                follow_up=detection.question_type is QuestionType.FOLLOW_UP,
            )
            if question is not None:
                self.resolver.register(
                    source=entry.source,
                    question_id=parent_id or question.id,
                    question_type=detection.question_type,
                )
            return question, detection
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None, None

    def submit_text(self, text: str, *, source: str = "loopback") -> tuple[Question | None, Detection | None]:
        """手动入口：与真实 asr.final 走同一个 ingest_final。"""
        segment_id = f"manual_{abs(hash(text)) & 0xFFFFFFFF:x}"
        return self.ingest_final(segment_id=segment_id, source=source, text=text)

    def confirm(self, question_id: str) -> Question:
        return self.store.transition(question_id, QuestionState.CONFIRMED)

    def ignore(self, question_id: str) -> Question:
        return self.store.transition(question_id, QuestionState.IGNORED)

    def answer(self, question_id: str) -> Question:
        return self.store.transition(question_id, QuestionState.ANSWERED)

    def cancel_all(self) -> int:
        return self.store.cancel_all()

    def metrics(self) -> dict[str, object]:
        current = self.store.current()
        history = self.store.history()
        return {
            "currentId": current.id if current else None,
            "currentType": current.question_type.value if current else None,
            "currentState": current.state.value if current else None,
            "questions": len(history),
            "byState": {
                state.value: sum(1 for item in history if item.state is state)
                for state in {
                    item.state for item in history
                }
            },
            "lastError": self.last_error,
        }

    def snapshot(self) -> dict[str, object]:
        current = self.store.current()
        return {
            "current": current,
            "history": self.store.history(),
            "transcriptCount": len(self.transcripts.recent(limit=10_000)),
        }

