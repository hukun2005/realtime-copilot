"""P3.2：ASR Manager、Provider、票据与结果校验。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from copilot_core.asr import (
    AsrAuthError,
    AsrManager,
    AsrProviderConfig,
    AsrResult,
    AsrState,
    DeepgramProvider,
    MockAsrProvider,
    SpeechSegment,
)
from copilot_core.asr.vad import SOURCES
from copilot_core.tickets import TicketIssuer, TicketRejection


def _text(value: object) -> str:
    return str(getattr(value, "root", value))


def segment(segment_id: str, source: str = "loopback") -> SpeechSegment:
    return SpeechSegment(
        segment_id=segment_id,
        source=source,
        start_ts_ns=1_000_000_000,
        end_ts_ns=2_000_000_000,
        frames=50,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-12.0,
        rms_dbfs=-20.0,
    )


def result(segment_id: str, revision: int, text: str, *, final: bool, source: str = "loopback") -> AsrResult:
    return AsrResult(
        segment_id=segment_id,
        source=source,
        revision=revision,
        text=text,
        start_ts_ns=1_000_000_000,
        end_ts_ns=2_000_000_000,
        final=final,
    )


class FakePipeline:
    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue[SpeechSegment]] = {
            source: asyncio.Queue() for source in SOURCES
        }

    async def get_segment(self, source: str) -> SpeechSegment:
        return await self.queues[source].get()

    def push(self, item: SpeechSegment) -> None:
        self.queues[item.source].put_nowait(item)


class Collector:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any) -> None:
        self.events.append(event)

    def of(self, event_type: str) -> list[Any]:
        return [event for event in self.events if event.type == event_type]


async def wait_for(predicate, *, seconds: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


def make_manager(
    *,
    provider: MockAsrProvider,
    generation: int = 1,
    collector: Collector | None = None,
    current_generation=None,
    timeout: float = 5.0,
) -> tuple[AsrManager, FakePipeline, Collector, TicketIssuer]:
    tickets = TicketIssuer()
    tickets.begin_session()
    pipeline = FakePipeline()
    collector = collector or Collector()
    manager = AsrManager(
        pipeline=pipeline,
        tickets=tickets,
        provider=provider,
        generation=generation,
        broadcast=collector,
        segment_timeout_seconds=timeout,
        current_generation=current_generation,
    )
    return manager, pipeline, collector, tickets


async def test_start_ready_and_ticket_consumed_once() -> None:
    provider = MockAsrProvider(results=[])
    manager, _, collector, tickets = make_manager(provider=provider)

    assert await manager.start()
    assert manager.state is AsrState.READY
    assert provider.connected
    assert collector.of("asr.status")
    assert provider.tickets
    replay = tickets.consume(provider.tickets[0], purpose="asr", generation=1)
    assert not replay.ok
    assert replay.reason is TicketRejection.REPLAYED
    await manager.stop()


async def test_partial_then_final_events() -> None:
    provider = MockAsrProvider(
        results=[
            result("seg_1", 0, "你", final=False),
            result("seg_1", 1, "你好", final=False),
            result("seg_1", 2, "你好。", final=True),
        ]
    )
    manager, pipeline, collector, _ = make_manager(provider=provider)
    await manager.start()
    pipeline.push(segment("seg_1"))

    await wait_for(lambda: len(collector.of("asr.final")) == 1)
    partials = collector.of("asr.partial")
    assert [event.payload.revision for event in partials] == [0, 1]
    final = collector.of("asr.final")[0]
    assert final.payload.text == "你好。"
    assert final.payload.start_ts_ns == 1_000_000_000
    await manager.stop()


async def test_stale_partial_after_final_is_rejected() -> None:
    provider = MockAsrProvider(results=[])
    manager, _, collector, _ = make_manager(provider=provider)
    await manager.start()
    item = segment("seg_2")

    await manager._accept(result("seg_2", 1, "定稿", final=True), item)
    await manager._accept(result("seg_2", 0, "旧", final=False), item)

    errors = collector.of("asr.error")
    assert errors and _text(errors[-1].payload.code) == "asr_segment_conflict"
    await manager.stop()


async def test_timeout_reports_error_and_stays_ready() -> None:
    provider = MockAsrProvider(
        results=[result("seg_3", 0, "慢", final=True)],
        delay_seconds=0.3,
    )
    manager, pipeline, collector, _ = make_manager(provider=provider, timeout=0.05)
    await manager.start()
    pipeline.push(segment("seg_3"))

    await wait_for(lambda: bool(collector.of("asr.error")))
    assert _text(collector.of("asr.error")[0].payload.code) == "asr_timeout"
    assert manager.state is AsrState.READY
    await manager.stop()


async def test_connect_failure_enters_error() -> None:
    provider = MockAsrProvider(fail_connect=True)
    manager, _, collector, _ = make_manager(provider=provider)

    assert not await manager.start()
    assert manager.state is AsrState.ERROR
    assert manager.snapshot()["errorCode"] == "asr_unavailable"
    assert _text(collector.of("asr.status")[-1].payload.state) == "error"


async def test_auth_failure_enters_error() -> None:
    provider = MockAsrProvider(fail_auth=True)
    manager, _, _, _ = make_manager(provider=provider)

    assert not await manager.start()
    assert manager.snapshot()["errorCode"] == "asr_auth_failed"


async def test_stale_generation_is_rejected() -> None:
    provider = MockAsrProvider()
    manager, _, _, _ = make_manager(provider=provider, generation=1, current_generation=lambda: 2)

    assert not await manager.start()
    assert manager.state is AsrState.ERROR
    assert manager.snapshot()["errorCode"] == "asr_stale_generation"
    assert not provider.connected


async def test_two_sources_are_consumed_independently() -> None:
    provider = MockAsrProvider(
        results=[
            result("seg_l", 0, "左", final=True, source="loopback"),
            result("seg_m", 0, "右", final=True, source="mic"),
        ]
    )
    manager, pipeline, collector, _ = make_manager(provider=provider)
    await manager.start()
    pipeline.push(segment("seg_l", "loopback"))
    pipeline.push(segment("seg_m", "mic"))

    await wait_for(lambda: len(collector.of("asr.final")) == 2)
    sources = {_text(event.payload.source) for event in collector.of("asr.final")}
    assert sources == {"loopback", "mic"}
    await manager.stop()


async def test_stop_is_idempotent_and_releases_provider() -> None:
    provider = MockAsrProvider()
    manager, _, collector, _ = make_manager(provider=provider)
    await manager.start()
    await manager.stop()
    await manager.stop()

    assert provider.closed
    assert manager.state is AsrState.IDLE
    assert manager._tasks == []
    assert _text(collector.of("asr.status")[-1].payload.state) == "idle"


async def test_cancel_current_delegates_to_provider() -> None:
    provider = MockAsrProvider()
    manager, _, _, _ = make_manager(provider=provider)
    await manager.start()
    await manager.cancel_current()
    assert provider.cancelled
    await manager.stop()


async def test_results_after_stop_are_not_published() -> None:
    class SlowProvider(MockAsrProvider):
        async def transcribe(self, item):  # type: ignore[override]
            await asyncio.sleep(0.2)
            yield result(item.segment_id, 0, "迟到", final=True)

    provider = SlowProvider()
    manager, pipeline, collector, _ = make_manager(provider=provider)
    await manager.start()
    pipeline.push(segment("seg_late"))
    await asyncio.sleep(0.02)
    await manager.stop()
    before = len(collector.of("asr.final"))
    await asyncio.sleep(0.35)

    assert len(collector.of("asr.final")) == before


def test_deepgram_registered_and_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASR_API_KEY", raising=False)
    with pytest.raises(AsrAuthError):
        DeepgramProvider(AsrProviderConfig(name="deepgram", api_key_env="ASR_API_KEY"))


def test_provider_registry_lists_deepgram() -> None:
    from copilot_core.asr import AsrRegistry

    assert "deepgram" in AsrRegistry.names()


def test_transcribe_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        provider = MockAsrProvider(
            results=[result("seg_err", 0, "x", final=True)],
            fail_transcribe=True,
        )
        manager, pipeline, collector, _ = make_manager(provider=provider)
        await manager.start()
        pipeline.push(segment("seg_err"))
        await wait_for(lambda: bool(collector.of("asr.error")))
        assert _text(collector.of("asr.error")[0].payload.code) == "asr_failed"
        await manager.stop()

    asyncio.run(scenario())
