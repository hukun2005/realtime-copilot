"""P4 集成：ASR final 事件进入问题检测，partial 不触发。"""

from __future__ import annotations

from copilot_core.app import create_app
from fastapi import FastAPI

from copilot_protocol import make_event


def final(text: str):
    return make_event(
        "asr.final",
        {
            "segmentId": "seg_1",
            "source": "loopback",
            "startTsNs": 0,
            "endTsNs": 1,
            "revision": 1,
            "text": text,
        },
    )


def partial(text: str):
    return make_event(
        "asr.partial",
        {
            "segmentId": "seg_1",
            "source": "loopback",
            "startTsNs": 0,
            "endTsNs": 1,
            "revision": 1,
            "text": text,
        },
    )


async def test_final_enters_detection_partial_does_not(config, token, data_dir, monkeypatch) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")

    async with app.router.lifespan_context(app):
        await app.state.asr_events(partial("什么是 Vue3？"))
        assert app.state.detection.metrics()["questions"] == 0

        await app.state.asr_events(final("什么是 Vue3？"))
        metrics = app.state.detection.metrics()
        assert metrics["questions"] == 1
        assert metrics["currentType"] == "technical"
        assert metrics["currentState"] == "candidate"
