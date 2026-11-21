"""P3.2.1：ASR 会话级生命周期与 generation 隔离。"""

from __future__ import annotations

import asyncio

from copilot_core.asr import AsrCoordinator, AsrState, MockAsrProvider, SpeechSegment
from copilot_core.asr.vad import SOURCES
from copilot_core.tickets import TicketIssuer


def segment(segment_id: str, source: str = "loopback") -> SpeechSegment:
    return SpeechSegment(
        segment_id=segment_id,
        source=source,
        start_ts_ns=0,
        end_ts_ns=1,
        frames=1,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-20,
        rms_dbfs=-30,
    )


class FakePipeline:
    def __init__(self) -> None:
        self.queues = {source: asyncio.Queue() for source in SOURCES}
        self.released: list[SpeechSegment] = []

    async def get_segment(self, source: str) -> SpeechSegment:
        return await self.queues[source].get()

    def release_segment(self, item: SpeechSegment) -> None:
        self.released.append(item)

    def push(self, item: SpeechSegment) -> None:
        self.queues[item.source].put_nowait(item)


class Collector:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def __call__(self, event: object) -> None:
        self.events.append(event)

    def finals(self) -> list[object]:
        return [event for event in self.events if getattr(event, "type", None) == "asr.final"]


async def wait_for(predicate, seconds: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


def make_coordinator(providers: list[MockAsrProvider]) -> tuple[AsrCoordinator, FakePipeline, Collector, TicketIssuer]:
    pipeline = FakePipeline()
    collector = Collector()
    tickets = TicketIssuer()
    tickets.begin_session()

    def factory() -> MockAsrProvider:
        provider = providers.pop(0)
        return provider

    coordinator = AsrCoordinator(
        pipeline=pipeline,
        tickets=tickets,
        provider_factory=factory,
        broadcast=collector,
    )
    return coordinator, pipeline, collector, tickets


async def test_start_creates_session_scoped_manager() -> None:
    provider = MockAsrProvider()
    coordinator, _, collector, _ = make_coordinator([provider])

    await coordinator.start(7)

    assert coordinator.generation == 7
    assert coordinator.manager is not None
    assert coordinator.manager.generation == 7
    assert coordinator.manager.state is AsrState.READY
    assert provider.connected
    assert collector.events
    await coordinator.stop()


async def test_stop_invalidates_generation_and_closes_provider() -> None:
    provider = MockAsrProvider()
    coordinator, _, _, _ = make_coordinator([provider])
    await coordinator.start(3)

    await coordinator.stop()

    assert coordinator.generation == 0
    assert coordinator.manager is None
    assert provider.closed
    await coordinator.stop()


async def test_new_session_restarts_with_new_provider() -> None:
    first = MockAsrProvider()
    second = MockAsrProvider()
    coordinator, _, _, _ = make_coordinator([first, second])

    await coordinator.start(1)
    await coordinator.start(2)

    assert first.closed
    assert second.connected
    assert coordinator.manager is not None
    assert coordinator.manager.generation == 2
    await coordinator.stop()


async def test_provider_factory_failure_does_not_raise() -> None:
    coordinator = AsrCoordinator(
        pipeline=FakePipeline(),
        tickets=TicketIssuer(),
        provider_factory=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        broadcast=Collector(),
    )

    await coordinator.start(1)

    assert coordinator.manager is None
    assert coordinator.generation == 0


async def test_results_are_released_after_processing() -> None:
    provider = MockAsrProvider()
    coordinator, pipeline, _, _ = make_coordinator([provider])
    await coordinator.start(1)
    item = segment("seg_lease")
    pipeline.push(item)

    await wait_for(lambda: bool(pipeline.released))
    assert pipeline.released[0] is item
    await coordinator.stop()


async def test_old_session_results_cannot_be_published() -> None:
    provider = MockAsrProvider()
    coordinator, _, collector, _ = make_coordinator([provider])
    await coordinator.start(1)
    old_manager = coordinator.manager
    assert old_manager is not None

    await coordinator.stop()
    before = len(collector.finals())

    # 旧 Manager 已停止；直接调用其内部入口也不得广播。
    await old_manager._accept(
        __import__("copilot_core.asr", fromlist=["AsrResult"]).AsrResult(
            segment_id="seg_old",
            source="loopback",
            revision=0,
            text="旧结果",
            start_ts_ns=0,
            end_ts_ns=1,
            final=True,
        ),
        segment("seg_old"),
    )
    await asyncio.sleep(0.05)

    assert len(collector.finals()) == before
