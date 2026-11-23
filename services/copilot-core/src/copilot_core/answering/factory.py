"""P51-05：SSE Answer 运行时装配。"""

from __future__ import annotations

from dataclasses import dataclass

from ..detection.llm import DeepSeekAnswerProvider, DeepSeekClient
from ..knowledge import KnowledgeStore
from .answer_runtime import AnswerRuntime
from .events import RuntimeEvent
from .rag import RagContextBuilder
from .review import ReviewService, deepseek_review_call


@dataclass
class AnswerStack:
    runtime: AnswerRuntime
    client: DeepSeekClient

    async def close(self) -> None:
        await self.runtime.close()
        await self.client.close()


def build_answer_stack(
    *,
    store: KnowledgeStore,
    publish,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    transport=None,
    enable_review: bool = True,
) -> AnswerStack:
    """复用 DeepSeekClient/Provider/Reviewer 与 RAG，不复制实现。"""
    client = DeepSeekClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        transport=transport,
    )
    reviewer = ReviewService(call=deepseek_review_call(client.complete)) if enable_review else None
    runtime = AnswerRuntime(
        provider=DeepSeekAnswerProvider(client),
        publish=publish,
        context_factory=RagContextBuilder(store).build,
        reviewer=reviewer,
    )
    return AnswerStack(runtime=runtime, client=client)


async def collect_events(events: list[RuntimeEvent]):
    async def publish(event: RuntimeEvent) -> None:
        events.append(event)

    return publish
