"""FastAPI 应用装配：``/healthz``、``/ws/v1/events`` 与采集控制面。

应用本身不负责绑定端口（见 :mod:`copilot_core.runtime`），以便测试直接注入
配置与临时数据目录。
"""

from __future__ import annotations

import hmac
import os
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, WebSocket
from fastapi.responses import JSONResponse

from copilot_protocol import PROTOCOL_VERSION, event_to_wire, make_asr_status

from .answering import AnswerEventPublisher, FinalGuard, build_answer_stack
from .asr import AsrCoordinator, AsrProviderConfig, AsrRegistry, AsrStatusTracker
from .audio import AudioHub
from .capture import CaptureController
from .config import AppConfig
from .detection import QuestionEngine
from .knowledge import KnowledgeStore
from .logging_setup import get_logger
from .memory import process_memory
from .rag import build_rag_context
from .resume import ResumeFileError, ResumeProjectUpdate, ResumeStore, ingest_resume, sanitize_filename
from .security import build_runtime_info, remove_runtime_file, write_runtime_file
from .storage import close, open_database
from .tickets import TicketIssuer
from .transcript import (
    TranscriptAssembler,
    TranscriptBuffer,
    TranscriptHandler,
    TranscriptStore,
)
from .ws.connection import EventConnection

logger = get_logger("copilot_core.app")


def create_app(
    config: AppConfig,
    *,
    token: str,
    data_dir: Path,
    version: str,
    runtime_path: Path | None = None,
) -> FastAPI:
    """构造 FastAPI 应用。

    Args:
        config: 已校验配置。
        token: 本次运行的鉴权令牌。
        data_dir: 数据目录（SQLite 与 ``runtime.json`` 的默认位置）。
        version: 服务版本。
        runtime_path: 若非 ``None``，启动时写入、退出时删除该运行时文件。

    Returns:
        配置好的应用；``/docs`` 等自动文档已关闭，减少暴露面。
    """
    ui_connections: set[EventConnection] = set()

    async def broadcast(event: Any) -> None:
        """把状态事件广播给所有 UI/bridge 连接（尽力而为）。"""
        for connection in list(ui_connections):
            try:
                await connection.send_event(event)
            except Exception:
                ui_connections.discard(connection)

    async def asr_events(event: Any) -> None:
        """广播 ASR 事件，再依次交给转写与问题检测。"""
        await broadcast(event)
        handler = getattr(app.state, "transcript_handler", None)
        if handler is not None:
            try:
                await handler.handle(event)
            except Exception:
                logger.exception("transcript handler failed")
        if getattr(event, "type", None) != "asr.final":
            return
        try:
            wire = event_to_wire(event)
            payload = wire["payload"]
            question, _detection = app.state.detection.ingest_final(
                segment_id=payload["segmentId"],
                source=payload["source"],
                text=payload["text"],
            )
            if question is None:
                return
            runtime = getattr(app.state, "answer_runtime", None)
            if runtime is None:
                return
            session_id = getattr(handler, "session_id", None) or "standalone"
            if not app.state.final_guard.accept(
                event_id=str(wire.get("eventId") or ""),
                session_id=session_id,
                source=payload["source"],
                segment_id=payload["segmentId"],
                text=payload["text"],
            ):
                return
            await runtime.submit_question(question, session_id=session_id)
        except Exception:
            logger.exception("question runtime failed")

    tickets = TicketIssuer(ttl_seconds=config.capture.ticket_ttl_seconds)
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.audio.pipeline.start()
        database = open_database(config.database_path(data_dir))
        app.state.db = database
        app.state.transcript_handler = TranscriptHandler(
            assembler=TranscriptAssembler(),
            buffer=TranscriptBuffer(),
            store=TranscriptStore(database),
            broadcast=broadcast,
            logger=get_logger("copilot_core.transcript"),
        )
        answer_stack = None
        if os.environ.get("DEEPSEEK_API_KEY"):
            try:
                answer_stack = build_answer_stack(
                    store=KnowledgeStore(database),
                    publish=app.state.answer_publisher.publish,
                )
                app.state.answer_runtime = answer_stack.runtime
                app.state.answer_stack = answer_stack
            except Exception:
                logger.exception("answer runtime creation failed")
        app.state.started_at = time.monotonic()
        if runtime_path is not None:
            info = build_runtime_info(
                version=version,
                protocol=PROTOCOL_VERSION,
                host=config.server.host,
                port=int(getattr(app.state, "port", 0)),
                token=token,
                mode=config.mode,
                data_dir=data_dir,
            )
            hardened = write_runtime_file(runtime_path, info)
            logger.info(
                "runtime file written",
                extra={"path": str(runtime_path), "hardened": hardened, "mode": config.mode},
            )
        logger.info("core started", extra={"version": version, "database": str(config.database_path(data_dir))})
        try:
            yield
        finally:
            if getattr(app.state, "answer_stack", None) is not None:
                await app.state.answer_stack.close()
            await controller.shutdown()
            if asr_coordinator is not None:
                await asr_coordinator.stop()
            handler = getattr(app.state, "transcript_handler", None)
            if handler is not None:
                await handler.stop(reason="shutdown")
            await app.state.audio.pipeline.stop()
            await broadcast(make_asr_status(state=app.state.asr.state.value, since=app.state.asr.since_ms))
            close(database)
            if runtime_path is not None:
                remove_runtime_file(runtime_path)
            logger.info("core stopped")

    app = FastAPI(
        title="RealTime Copilot Core",
        version=version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.connections = 0
    app.state.roles = {}
    app.state.port = 0
    app.state.audio = AudioHub(config.audio)
    app.state.detection = QuestionEngine()
    app.state.final_guard = FinalGuard()
    app.state.answer_publisher = AnswerEventPublisher()
    app.state.answer_runtime = None
    asr_coordinator: AsrCoordinator | None = None
    provider_name = os.environ.get("RTC_ASR_PROVIDER")
    if provider_name:
        try:
            key_env = (
                "DASHSCOPE_API_KEY"
                if provider_name == "aliyun"
                else "DEEPGRAM_API_KEY"
                if provider_name == "deepgram"
                else "ASR_API_KEY"
            )
            provider_config = AsrProviderConfig(
                name=provider_name,
                language=os.environ.get("RTC_ASR_LANGUAGE", "zh-CN"),
                api_key_env=key_env,
                model=os.environ.get("RTC_ASR_MODEL") or None,
                workspace_id=(
                    os.environ.get("DASHSCOPE_WORKSPACE_ID")
                    or os.environ.get("RTC_ASR_WORKSPACE_ID")
                ),
            )
            asr_coordinator = AsrCoordinator(
                pipeline=app.state.audio.pipeline,
                tickets=tickets,
                provider_factory=lambda: AsrRegistry.create(provider_config),
                broadcast=asr_events,
                logger=get_logger("copilot_core.asr"),
            )
        except Exception:
            get_logger("copilot_core.asr").exception("asr coordinator creation failed")
    app.state.asr_coordinator = asr_coordinator

    async def on_capture_start(generation: int) -> None:
        handler = getattr(app.state, "transcript_handler", None)
        if handler is not None:
            try:
                await handler.start(
                    f"sess_{uuid.uuid4().hex}",
                    generation=generation,
                    mode=config.mode,
                )
            except Exception:
                logger.exception("transcript session start failed")
        if asr_coordinator is not None:
            await asr_coordinator.start(generation)

    async def on_capture_stop() -> None:
        handler = getattr(app.state, "transcript_handler", None)
        runtime = getattr(app.state, "answer_runtime", None)
        if runtime is not None and handler is not None and handler.session_id is not None:
            try:
                await runtime.cancel_session(handler.session_id)
            except Exception:
                logger.exception("answer session cancel failed")
        try:
            if asr_coordinator is not None:
                await asr_coordinator.stop()
        finally:
            handler = getattr(app.state, "transcript_handler", None)
            if handler is not None:
                try:
                    await handler.stop(reason="capture_stopped")
                except Exception:
                    logger.exception("transcript session stop failed")

    controller = CaptureController(
        config=config.capture,
        tickets=tickets,
        logger=get_logger("copilot_core.capture"),
        broadcast=broadcast,
        source_stats=lambda: app.state.audio.snapshot()["sources"],
        on_capture_start=on_capture_start,
        on_capture_stop=on_capture_stop,
    )

    app.state.asr = AsrStatusTracker()
    app.state.capture = controller
    app.state.asr_events = asr_events
    app.state.transcript_start = on_capture_start
    app.state.transcript_stop = on_capture_stop
    app.state.transcript_handler = None

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """健康检查。

        Returns:
            200 表示数据库可用；503 表示降级。
        """
        started_at = getattr(app.state, "started_at", None)
        uptime_ms = int((time.monotonic() - started_at) * 1000) if started_at is not None else 0
        database_ok = True
        try:
            app.state.db.execute("SELECT 1")
        except sqlite3.Error:
            database_ok = False
        try:
            body = {
            "status": "ok" if database_ok else "degraded",
            "version": version,
            "protocol": PROTOCOL_VERSION,
            "mode": config.mode,
            "uptimeMs": uptime_ms,
            "connections": int(app.state.connections),
            "roles": dict(app.state.roles),
            "audio": app.state.audio.snapshot(),
            "capture": controller.snapshot(),
            "asr": (
                app.state.asr_coordinator.snapshot()
                if getattr(app.state, "asr_coordinator", None) is not None
                else None
            ),
            "transcript": (
                app.state.transcript_handler.snapshot()
                if getattr(app.state, "transcript_handler", None) is not None
                else None
            ),
            "process": process_memory(),
            "database": "ok" if database_ok else "error",
            }
            return JSONResponse(body, status_code=200 if database_ok else 503)
        except Exception:
            logger.exception("healthz failed")
            return JSONResponse({"status": "error", "error": "internal_error"}, status_code=500)

    async def require_resume_token(x_copilot_token: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_copilot_token, token):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/api/answer/status")
    def answer_status(_: None = Depends(require_resume_token)) -> dict[str, Any]:
        stack = getattr(app.state, "answer_stack", None)
        if stack is None:
            return {"configured": False, "state": "NOT_CONFIGURED", "model": None, "lastError": None}
        error = stack.client.last_error
        return {
            "configured": True,
            "state": error.code.upper() if error is not None else "CONFIGURED",
            "model": stack.client.model,
            "lastError": None if error is None else {"code": error.code, "status": error.status, "retryable": error.retryable},
        }

    @app.post("/api/answers/cancel")
    async def cancel_answer(payload: dict[str, Any], _: None = Depends(require_resume_token)) -> dict[str, bool]:
        request_id = str(payload.get("requestId") or "").strip()
        if not request_id:
            raise HTTPException(status_code=400, detail="request_id_required")
        runtime = getattr(app.state, "answer_runtime", None)
        if runtime is None:
            raise HTTPException(status_code=503, detail="answer_runtime_not_configured")
        return {"cancelled": await runtime.cancel(request_id, reason="user_cancelled")}

    @app.get("/api/knowledge/search")
    def search_knowledge(
        q: str,
        project_id: str | None = None,
        source_type: str | None = None,
        limit: int = 10,
        _: None = Depends(require_resume_token),
    ) -> list[dict[str, Any]]:
        return KnowledgeStore(app.state.db).search(
            q, project_id=project_id, source_type=source_type, limit=min(limit, 50)
        )

    @app.post("/api/rag/context")
    def rag_context(
        payload: dict[str, Any],
        _: None = Depends(require_resume_token),
    ) -> dict[str, Any]:
        text = str(payload.get("question") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="question_required")
        engine = QuestionEngine(generation=1)
        question, detection = engine.submit_text(text)
        rag = build_rag_context(
            query=text,
            store=KnowledgeStore(app.state.db),
            project_id=payload.get("projectId") or None,
        )
        return {
            "detected": bool(detection and detection.is_question),
            "projectEvidence": rag.project_evidence,
            "technicalKnowledge": rag.technical_knowledge,
            "citations": rag.citations,
            "missing": rag.missing,
            "questionId": question.id if question else None,
        }

    @app.get("/api/resumes")
    def list_resumes(_: None = Depends(require_resume_token)) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in ResumeStore(app.state.db).list()]

    @app.get("/api/resumes/active")
    def active_resume(_: None = Depends(require_resume_token)) -> dict[str, Any]:
        resume = ResumeStore(app.state.db).active()
        if resume is None:
            raise HTTPException(status_code=404, detail="no active resume")
        return resume.model_dump(mode="json")

    @app.post("/api/resumes")
    async def upload_resume(file: UploadFile, _: None = Depends(require_resume_token)) -> dict[str, Any]:
        raw = await file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="file_too_large")
        safe = sanitize_filename(file.filename or "resume")
        directory = data_dir / "resumes"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{uuid.uuid4().hex}_{safe}"
        target.write_bytes(raw)
        try:
            resume = ingest_resume(target, file_name=safe, store=ResumeStore(app.state.db))
        except ResumeFileError as error:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=error.code) from error
        return resume.model_dump(mode="json")

    @app.get("/api/resumes/{resume_id}")
    def get_resume(resume_id: str, _: None = Depends(require_resume_token)) -> dict[str, Any]:
        resume = ResumeStore(app.state.db).get(resume_id)
        if resume is None:
            raise HTTPException(status_code=404, detail="resume_not_found")
        return resume.model_dump(mode="json")

    @app.delete("/api/resumes/{resume_id}")
    def delete_resume(resume_id: str, _: None = Depends(require_resume_token)) -> dict[str, bool]:
        if not ResumeStore(app.state.db).delete(resume_id):
            raise HTTPException(status_code=404, detail="resume_not_found")
        return {"deleted": True}

    @app.post("/api/resumes/{resume_id}/activate")
    def activate_resume(resume_id: str, _: None = Depends(require_resume_token)) -> dict[str, bool]:
        if not ResumeStore(app.state.db).set_active(resume_id):
            raise HTTPException(status_code=404, detail="resume_not_found")
        return {"active": True}

    @app.patch("/api/resume-projects/{project_id}")
    def update_resume_project(project_id: str, update: ResumeProjectUpdate, _: None = Depends(require_resume_token)) -> dict[str, Any]:
        project = ResumeStore(app.state.db).update_project(project_id, update)
        if project is None:
            raise HTTPException(status_code=404, detail="project_not_found")
        return project.model_dump(mode="json")

    @app.websocket("/ws/v1/events")
    async def events(websocket: WebSocket) -> None:
        """事件通道：一条连接对应一个客户端角色。"""

        app.state.connections += 1
        connection: EventConnection | None = None

        def on_authenticated(role: str) -> None:
            app.state.roles[role] = app.state.roles.get(role, 0) + 1
            if role in ("ui", "bridge") and connection is not None:
                ui_connections.add(connection)

        connection = EventConnection(
            websocket,
            config=config,
            token=token,
            version=version,
            logger=logger,
            on_authenticated=on_authenticated,
            audio_hub=app.state.audio,
            capture=controller,
            tickets=tickets,
            answer_publisher=app.state.answer_publisher,
            answer_session=lambda: getattr(app.state.transcript_handler, "session_id", None),
        )
        try:
            await connection.run()
        finally:
            ui_connections.discard(connection)
            app.state.connections -= 1
            role = connection.role
            if role is not None:
                app.state.roles[role] = max(0, app.state.roles.get(role, 1) - 1)

    return app
