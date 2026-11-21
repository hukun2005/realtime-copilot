"""P3.2.3：ASR 生命周期竞态、shutdown 与 generation 隔离。"""

from __future__ import annotations

import asyncio

from copilot_core.asr import AsrCoordinator, MockAsrProvider, SpeechSegment
from copilot_core.asr.vad import SOURCES
from copilot_core.capture.controller import CaptureController
from copilot_core.config import CaptureConfig
from copilot_core.logging_setup import get_logger
from copilot_core.memory import process_memory
from copilot_core.tickets import TicketIssuer


class GatedProvider(MockAsrProvider):
    def __init__(self, gate: asyncio.Event) -> None:
        super().__init__()
        self._gate = gate

    async def connect(self, ticket: str) -> None:
        await self._gate.wait()
        await super().connect(ticket)


class FakePipeline:
    def __init__(self) -> None:
        self.queues = {source: asyncio.Queue() for source in SOURCES}

    async def get_segment(self, source: str) -> SpeechSegment:
        return await self.queues[source].get()

    def release_segment(self, item: SpeechSegment) -> None:
        return None


class Collector:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def __call__(self, event: object) -> None:
        self.events.append(event)


def make_coordinator(providers: list[MockAsrProvider]) -> AsrCoordinator:
    def factory() -> MockAsrProvider:
        return providers.pop(0)

    tickets = TicketIssuer()
    tickets.begin_session()
    return AsrCoordinator(
        pipeline=FakePipeline(),
        tickets=tickets,
        provider_factory=factory,
        broadcast=Collector(),
    )


async def test_stop_during_blocked_start_never_leaves_manager() -> None:
    gate = asyncio.Event()
    provider = GatedProvider(gate)
    coordinator = make_coordinator([provider])

    start_task = asyncio.create_task(coordinator.start(1))
    await asyncio.sleep(0.02)
    await coordinator.stop()
    gate.set()
    await start_task

    assert coordinator.manager is None
    assert provider.closed
    assert coordinator.active_tasks == 0


async def test_start_stop_start_creates_only_latest_generation() -> None:
    first = MockAsrProvider()
    second = MockAsrProvider()
    coordinator = make_coordinator([first, second])

    await coordinator.start(1)
    await coordinator.stop()
    await coordinator.start(2)

    assert first.closed
    assert second.connected
    assert coordinator.manager is not None
    assert coordinator.manager.generation == 2
    await coordinator.stop()


async def test_repeated_stop_is_idempotent() -> None:
    provider = MockAsrProvider()
    coordinator = make_coordinator([provider])
    await coordinator.start(1)
    await coordinator.stop()
    await coordinator.stop()
    assert coordinator.manager is None


async def test_shutdown_cancels_inflight_start() -> None:
    gate = asyncio.Event()
    provider = GatedProvider(gate)
    coordinator = make_coordinator([provider])

    start_task = asyncio.create_task(coordinator.start(1))
    await asyncio.sleep(0.02)
    await coordinator.shutdown()
    gate.set()
    await start_task  # start 已在内部捕获取消，不向外抛出

    assert coordinator.active_tasks == 0
    assert coordinator.manager is None
    assert provider.closed


async def test_shutdown_blocks_new_start() -> None:
    provider = MockAsrProvider()
    coordinator = make_coordinator([provider])
    await coordinator.shutdown()

    await coordinator.start(9)

    assert coordinator.manager is None
    assert not provider.connected


def make_controller(start_calls: list[int], stop_calls: list[int]) -> CaptureController:
    async def on_start(generation: int) -> None:
        start_calls.append(generation)

    async def on_stop() -> None:
        stop_calls.append(1)

    return CaptureController(
        config=CaptureConfig(sidecar_command=["unused"]),
        tickets=TicketIssuer(),
        logger=get_logger("test.asr"),
        broadcast=CoroutineNoop(),
        on_capture_start=on_start,
        on_capture_stop=on_stop,
    )


class CoroutineNoop:
    async def __call__(self, event: object) -> None:
        return None


async def test_controller_tracks_and_joins_asr_tasks() -> None:
    starts: list[int] = []
    stops: list[int] = []
    controller = make_controller(starts, stops)

    controller._notify_asr_start(3)
    await asyncio.sleep(0.02)
    controller._notify_asr_stop()
    await controller.wait_for_asr_tasks()

    assert starts == [3]
    assert stops == [1]
    assert not controller._asr_tasks


async def test_controller_shutdown_ignores_new_start() -> None:
    starts: list[int] = []
    stops: list[int] = []
    controller = make_controller(starts, stops)

    await controller.shutdown()
    controller._notify_asr_start(5)
    await asyncio.sleep(0.02)

    assert starts == []


def test_process_memory_is_separate_from_pcm_budget() -> None:
    data = process_memory()
    if data is None:
        return
    assert data["workingSetBytes"] > 0
    assert data["privateBytes"] > 0


async def test_coordinator_snapshot_exposes_tasks_and_metrics() -> None:
    provider = MockAsrProvider()
    coordinator = make_coordinator([provider])
    await coordinator.start(4)
    snapshot = coordinator.snapshot()

    assert snapshot["activeTasks"] == 0
    assert snapshot["generation"] == 4
    assert snapshot["state"] in {"ready", "idle"}
    await coordinator.stop()