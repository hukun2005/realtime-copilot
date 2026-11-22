"""P4 可选的真实 LLM Review / Answer（DeepSeek OpenAI 兼容接口）。

Key 只从环境变量读取，不写入仓库、日志或报告。
默认不启用：规则检测仍是主路径；只有显式构造这些类才会发起网络请求。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .answer import AnswerContext, AnswerResult, build_answer_messages
from .classifier import Detection, QuestionType
from .review import ReviewDecision
from .state import Question

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"


class LlmNotConfigured(RuntimeError):
    pass


class LlmError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise LlmNotConfigured("DEEPSEEK_API_KEY is not set")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def __aenter__(self) -> DeepSeekClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()


    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 400,
        temperature: float = 0.0,
    ) -> AsyncIterator[str]:
        """SSE 流式补全；调用方取消时关闭响应。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "thinking": {"type": "disabled"},
        }
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    raise LlmError(f"DeepSeek HTTP {response.status_code}")
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        if data_lines:
                            content = _parse_sse_data("\n".join(data_lines))
                            data_lines.clear()
                            if content == "__DONE__":
                                return
                            if content:
                                yield content
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    if field.strip() == "data":
                        data_lines.append(value.lstrip(" "))
                if data_lines:
                    content = _parse_sse_data("\n".join(data_lines))
                    if content and content != "__DONE__":
                        yield content
        except LlmError:
            raise
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise LlmError("DeepSeek stream timed out") from error
        except Exception as error:
            raise LlmError(f"DeepSeek stream failed: {type(error).__name__}") from error

    async def health_check(self) -> bool:
        try:
            response = await self._client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            return response.status_code == 200
        except Exception:
            return False

    def model_info(self) -> dict[str, str]:
        return {"provider": "deepseek", "model": self.model, "baseUrl": self.base_url}

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 300,
        temperature: float = 0.0,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            return str(body["choices"][0]["message"]["content"]).strip()
        except Exception as error:
            raise LlmError(f"DeepSeek request failed: {type(error).__name__}") from error


@dataclass
class DeepSeekReviewer:
    client: DeepSeekClient

    async def review(self, *, text: str, detection: Detection) -> ReviewDecision | None:
        prompt = (
            "你是面试问题检测复核器。根据文本和规则结果返回严格 JSON，不要解释：\n"
            '{"is_question": true/false, "type": "technical|project|behavioral|scenario|follow_up|general|statement", '
            '"confidence": 0.0-1.0, "reason": "short"}\n'
            f"规则结果: is_question={detection.is_question}, type={detection.question_type.value}\n"
            f"文本: {text}"
        )
        content = await self.client.complete(
            [
                {"role": "system", "content": "只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
        )
        return parse_review(content)


def parse_review(content: str) -> ReviewDecision | None:
    match = re.search(r"\{.*\}", content, re.S)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    raw_type = data.get("type")
    try:
        question_type = QuestionType(raw_type) if raw_type else None
    except ValueError:
        question_type = None
    confidence = data.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
    return ReviewDecision(
        is_question=bool(data["is_question"]) if "is_question" in data else None,
        question_type=question_type,
        confidence=confidence,
        reason=str(data.get("reason") or "")[:200] or None,
    )


def _parse_sse_data(data: str) -> str | None:
    """解析一条 SSE data；无效 JSON 视为协议错误。"""
    if data.strip() == "[DONE]":
        return "__DONE__"
    try:
        body = json.loads(data)
    except json.JSONDecodeError as error:
        raise LlmError("invalid SSE payload") from error
    if not isinstance(body, dict):
        raise LlmError("invalid SSE payload")
    choices = body.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return str(content) if content else None


@dataclass
class DeepSeekAnswerProvider:
    client: DeepSeekClient
    max_tokens: int = 400

    async def answer(self, *, question: Question, context: Sequence[Question] = ()) -> AnswerResult:
        context_text = "\n".join(item.text for item in context[-5:])
        prompt = (
            "你是面试助手。用口语化中文给出简短、可直接念出的答案，最多三句话。\n"
            f"最近上下文:\n{context_text or '(无)'}\n"
            f"问题: {question.text}"
        )
        started = time.monotonic()
        text = await self.client.complete(
            [
                {"role": "system", "content": "回答简洁，不编造事实。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.max_tokens,
        )
        return AnswerResult(
            text=text,
            provider=f"deepseek:{self.client.model}",
            latency_ms=round((time.monotonic() - started) * 1000, 1),
        )

    async def stream(self, *, question: Question, context: AnswerContext) -> AsyncIterator[str]:
        async for chunk in self.client.stream(
            build_answer_messages(context),
            max_tokens=self.max_tokens,
        ):
            yield chunk
