import asyncio
import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import anyio
from control_plane.infrastructure import db
from control_plane.infrastructure.secrets import SecretStoreUnavailable
from control_plane.modules import (
    case_search,
    catalogs,
    imports,
    model_configs,
    node_runtime,
    report_export,
    reports,
    service,
    tasks,
)
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from platform_contracts.execution_reports import MAX_STEP_FRAME_BYTES, ExecutionReport, FrameDigest
from platform_contracts.imports import (
    ImportAcceptance,
    ImportBatch,
    ImportPreviewRequest,
    ImportUpload,
)
from platform_contracts.model_configs import (
    ModelAvailability,
    ModelCheck,
    ModelCreate,
    ModelRecord,
    ModelSelection,
    ModelUpdate,
    ModelVersion,
)
from platform_contracts.models import (
    AdbObservation,
    CaseContent,
    CatalogCreate,
    CatalogKind,
    CatalogWrite,
    CreateCase,
    NodeEvent,
    NodeRegistration,
    NodeUpdate,
    OpenRuntime,
    StableSelection,
    SubmitRun,
)
from platform_contracts.tasks import (
    BatchDetail,
    BatchReport,
    CreateTask,
    ExecutionBatch,
    ReportPage,
    SaveTask,
    TaskDetail,
    TaskPage,
    TriggerTask,
)
from platform_identity.policy import authorize, current_subject
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

app = FastAPI(
    title="Kraken",
    version="0.1.0",
    description="M0 simulator foundation; real device execution disabled",
)


def subject():
    value = current_subject()
    authorize(value, "platform.use")
    return value


@app.exception_handler(service.Conflict)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={"error": exc.code, "detail": exc.detail})


@app.exception_handler(SecretStoreUnavailable)
async def credential_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith("/api/models"):
        # Pydantic errors normally echo the submitted input, including Key or URL credentials.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
        )
    return await request_validation_exception_handler(request, exc)


@app.get("/api/models", response_model=list[ModelRecord])
def models(who=Depends(subject)):
    with db.engine.connect() as conn:
        return model_configs.listing(conn)


@app.post("/api/models", response_model=ModelRecord, status_code=201)
def create_model(body: ModelCreate, who=Depends(subject)):
    with db.engine.begin() as conn:
        return model_configs.save(conn, body, who.id)


@app.put("/api/models/{config_id}", response_model=ModelRecord)
def update_model(config_id: UUID, body: ModelUpdate, who=Depends(subject)):
    with db.engine.begin() as conn:
        return model_configs.save(conn, body, who.id, config_id)


@app.put("/api/models/{config_id}/availability", response_model=ModelRecord)
def model_availability(config_id: UUID, body: ModelAvailability, who=Depends(subject)):
    with db.engine.begin() as conn:
        return model_configs.availability(conn, config_id, body, who.id)


@app.get("/api/models/{config_id}/versions", response_model=list[ModelVersion])
def model_versions(config_id: UUID, who=Depends(subject)):
    with db.engine.connect() as conn:
        return model_configs.versions(conn, config_id)


@app.post("/api/models/{config_id}/check", response_model=ModelCheck)
def check_model(config_id: UUID, body: ModelSelection, who=Depends(subject)):
    with db.engine.connect() as conn:
        definition, key = model_configs.probe_input(conn, config_id, body.expected_revision)
    result = model_configs.probe(definition, key)
    with db.engine.begin() as conn:
        return model_configs.save_probe(conn, config_id, body.expected_revision, result, who.id)


@app.get("/healthz")
def health():
    return {"status": "ok", "mode": "simulation"}


@app.get("/readyz")
def ready():
    from control_plane.entrypoints.worker import app as celery

    try:
        with db.engine.connect() as conn:
            db.execute(conn, "SELECT 1 FROM alembic_version")
    except Exception:
        raise HTTPException(503, "Database or migration is not ready") from None
    try:
        with celery.connection(connect_timeout=2) as connection:
            connection.ensure_connection(max_retries=0)
    except Exception:
        raise HTTPException(503, "Message broker is not ready") from None
    return {"database": "ready", "broker": "ready"}


@app.get("/api/me")
def me(who=Depends(subject)):
    return who


@app.post("/api/cases", status_code=201)
def new_case(body: CreateCase, who=Depends(subject)):
    with db.engine.begin() as conn:
        return service.create_case(conn, body.name, who.id, code=body.code)


@app.post("/api/cases/{case_id}/versions", status_code=201)
def version(
    case_id: UUID, body: CaseContent, expected_latest: str | None = None, who=Depends(subject)
):
    with db.engine.begin() as conn:
        if expected_latest is not None:
            db.one(conn, "SELECT id FROM cases WHERE id=:id FOR UPDATE", id=case_id)
            previous = imports.latest(conn, case_id)
            if (str(previous["id"]) if previous else "none") != expected_latest:
                raise service.Conflict(
                    "case_changed", "用例在编辑期间已有新版本，请保留修改并重新打开最新版本核对"
                )
        return service.add_version(conn, case_id, body.model_dump(), who.id)


@app.put("/api/cases/{case_id}/stable")
def stable(case_id: UUID, body: StableSelection, who=Depends(subject)):
    with db.engine.begin() as conn:
        return service.set_stable(
            conn, case_id, body.version_id, who.id, expected_version_id=body.expected_version_id
        )


@app.post("/api/imports/excel", response_model=ImportUpload, status_code=201)
async def upload_excel(
    request: Request, filename: str = Query(min_length=1, max_length=200), who=Depends(subject)
):
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(422, "请使用 .xlsx 文件；旧版 .xls 请先另存为 .xlsx")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > imports.MAX_FILE_BYTES:
            raise HTTPException(413, "文件不能超过 10 MB")
        raw.extend(chunk)
    try:
        workbook = await run_in_threadpool(imports.parse_workbook, bytes(raw))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with db.engine.begin() as conn:
        return imports.upload(
            conn, filename.replace("\\", "/").split("/")[-1], bytes(raw), workbook, who.id
        )


@app.get("/api/imports/uploads/{upload_id}", response_model=ImportUpload)
def import_upload(upload_id: UUID, who=Depends(subject)):
    with db.engine.connect() as conn:
        return imports.get_upload(conn, upload_id)


@app.get("/api/imports/uploads/{upload_id}/file")
def import_original(upload_id: UUID, who=Depends(subject)):
    from urllib.parse import quote

    with db.engine.connect() as conn:
        uploaded = db.one(
            conn, "SELECT filename,original FROM import_uploads WHERE id=:id", id=upload_id
        )
        if not uploaded:
            raise HTTPException(404, "上传文件不存在")
        return Response(
            bytes(uploaded["original"]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(uploaded['filename'], safe='')}"
            },
        )


@app.post("/api/imports/preview", response_model=ImportBatch, status_code=201)
def preview_import(body: ImportPreviewRequest, who=Depends(subject)):
    with db.engine.begin() as conn:
        return imports.preview(conn, body, who.id)


@app.get("/api/imports")
def import_history(who=Depends(subject)):
    with db.engine.connect() as conn:
        return imports.list_batches(conn)


@app.get("/api/imports/{batch_id}", response_model=ImportBatch)
def get_import(batch_id: UUID, who=Depends(subject)):
    with db.engine.connect() as conn:
        return imports.get_batch(conn, batch_id)


@app.post("/api/imports/{batch_id}/accept", response_model=ImportBatch)
def accept_import(batch_id: UUID, body: ImportAcceptance, who=Depends(subject)):
    with db.engine.begin() as conn:
        return imports.accept(conn, batch_id, body, who.id)


@app.post("/api/runs", status_code=201)
def run(body: SubmitRun, who=Depends(subject)):
    with db.engine.begin() as conn:
        return service.submit(conn, body, who.id)


@app.get("/api/tasks", response_model=TaskPage)
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = "",
    scheduled: bool = False,
    who=Depends(subject),
):
    with db.engine.connect() as conn:
        return tasks.list_tasks(conn, page, page_size, q, scheduled)


@app.post("/api/tasks", response_model=TaskDetail, status_code=201)
def create_task(body: CreateTask, who=Depends(subject)):
    with db.engine.begin() as conn:
        return tasks.create(conn, body, who.id)


@app.get("/api/tasks/{task_id}", response_model=TaskDetail)
def task_detail(task_id: UUID, who=Depends(subject)):
    with db.engine.connect() as conn:
        return tasks.detail(conn, task_id)


@app.put("/api/tasks/{task_id}", response_model=TaskDetail)
def save_task(task_id: UUID, body: SaveTask, who=Depends(subject)):
    with db.engine.begin() as conn:
        return tasks.save(conn, task_id, body, who.id)


@app.post("/api/tasks/{task_id}/execute", response_model=BatchDetail, status_code=201)
def execute_task(task_id: UUID, body: TriggerTask, who=Depends(subject)):
    with db.engine.begin() as conn:
        return tasks.trigger(conn, task_id, body.request_id, body.expected_revision, who.id)


@app.get("/api/executions", response_model=list[ExecutionBatch])
def executions(task_id: UUID | None = None, who=Depends(subject)):
    with db.engine.connect() as conn:
        return tasks.history(conn, task_id)


@app.get("/api/executions/{run_id}", response_model=BatchDetail)
def execution_detail(run_id: UUID, who=Depends(subject)):
    with db.engine.connect() as conn:
        return tasks.batch(conn, run_id)


@app.get("/api/reports", response_model=ReportPage)
def report_page(
    task_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    who=Depends(subject),
):
    with db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        return reports.page(conn, task_id, page, page_size)


@app.get("/api/reports/{run_id}", response_model=BatchReport)
def report_detail(run_id: UUID, who=Depends(subject)):
    with db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        return reports.detail(conn, run_id)


@app.post(
    "/api/reports/{run_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                media: {"schema": {"type": "string", "format": "binary"}}
                for media in report_export.MEDIA_TYPES.values()
            }
        }
    },
)
def export_report(run_id: UUID, format: Literal["html", "json"] = "html", who=Depends(subject)):
    with db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        batch = BatchReport.model_validate(reports.detail(conn, run_id))
    try:
        filename, document = report_export.export(batch, format)
    except OSError:
        raise HTTPException(503, "报告存储目录暂不可写，请联系平台管理员") from None
    return Response(
        document,
        media_type=report_export.MEDIA_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/executions/{run_id}/cancel", response_model=BatchDetail)
def cancel_execution(run_id: UUID, who=Depends(subject)):
    with db.engine.begin() as conn:
        return tasks.cancel_batch(conn, run_id, who.id)


@app.post("/api/attempts/{attempt_id}/cancel")
def cancel(attempt_id: UUID, who=Depends(subject)):
    with db.engine.begin() as conn:
        return service.cancel(conn, attempt_id, who.id)


@app.get("/api/attempts/{attempt_id}/report", response_model=ExecutionReport | None)
def attempt_report(attempt_id: UUID, who=Depends(subject)):
    with db.engine.connect() as conn:
        row = db.one(
            conn,
            """SELECT r.payload FROM attempts a LEFT JOIN execution_reports r ON r.attempt_id=a.id
            WHERE a.id=:id""",
            id=attempt_id,
        )
        if row is None:
            raise HTTPException(404, "执行尝试不存在")
        return row["payload"]


@app.put("/api/nodes/{node_id}/execution-reports/{command_id}", status_code=204)
def upload_execution_report(
    node_id: str,
    command_id: UUID,
    body: ExecutionReport,
):
    with db.engine.begin() as conn:
        attempt_id = report_attempt(conn, node_id, command_id)
        db.execute(
            conn,
            """INSERT INTO execution_reports(attempt_id,payload) VALUES (:id,CAST(:p AS jsonb))
            ON CONFLICT (attempt_id) DO UPDATE SET payload=EXCLUDED.payload
            WHERE execution_reports.payload->>'visual_version' IS NULL
            AND EXCLUDED.payload->>'visual_version'='1'""",
            id=attempt_id,
            p=body.model_dump_json(),
        )
    return Response(status_code=204)


def report_attempt(conn, node_id, command_id):
    command = db.one(
        conn,
        """SELECT c.attempt_id FROM commands c JOIN attempts a ON a.id=c.attempt_id
        JOIN leases l ON l.attempt_id=a.id JOIN devices d ON d.id=l.device_id
        WHERE c.id=:cmd AND c.node_id=:node AND d.node_id=:node AND c.accepted
        AND c.payload->>'kind'='execute' AND NOT a.simulation
        AND l.generation=CAST(c.payload->>'lease_generation' AS bigint)
        AND d.serial=c.payload->>'serial' FOR UPDATE OF a""",
        cmd=command_id,
        node=node_id,
    )
    if not command:
        raise HTTPException(404, "执行指令与节点不匹配")
    return command["attempt_id"]


@app.put("/api/nodes/{node_id}/execution-reports/{command_id}/frames/{digest}", status_code=204)
async def upload_execution_frame(
    node_id: str,
    command_id: UUID,
    digest: FrameDigest,
    request: Request,
):
    image = bytearray()
    async for chunk in request.stream():
        if len(image) + len(chunk) > MAX_STEP_FRAME_BYTES:
            raise HTTPException(413, "截图超过大小上限")
        image.extend(chunk)
    if not image.startswith(b"\xff\xd8\xff") or hashlib.sha256(image).hexdigest() != digest:
        raise HTTPException(422, "截图格式或校验值有误")

    def store():
        with db.engine.begin() as conn:
            attempt_id = report_attempt(conn, node_id, command_id)
            db.execute(
                conn,
                """INSERT INTO execution_frames(attempt_id,digest,image)
                VALUES (:id,:d,:image) ON CONFLICT DO NOTHING""",
                id=attempt_id,
                d=digest,
                image=bytes(image),
            )

    await run_in_threadpool(store)
    return Response(status_code=204)


@app.get("/api/attempts/{attempt_id}/report/frames/{digest}")
def execution_frame(attempt_id: UUID, digest: FrameDigest, who=Depends(subject)):
    with db.engine.connect() as conn:
        frame = db.one(
            conn,
            "SELECT image FROM execution_frames WHERE attempt_id=:id AND digest=:d",
            id=attempt_id,
            d=digest,
        )
    if not frame:
        raise HTTPException(404, "截图不存在")
    return Response(
        bytes(frame["image"]),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/attempts/{attempt_id}/retry", status_code=201)
def retry(attempt_id: UUID, who=Depends(subject)):
    with db.engine.begin() as conn:
        return service.retry(conn, attempt_id, who.id)


class CaseRecord(BaseModel):
    id: UUID
    code: str
    name: str
    stable_version_id: UUID | None


class VersionRecord(BaseModel):
    id: UUID
    case_id: UUID
    content: CaseContent
    created_at: datetime


class CatalogRecord(BaseModel):
    id: UUID
    kind: CatalogKind
    name: str
    description: str
    parent_id: UUID | None
    enabled: bool
    reference_count: int = 0


class CaseListRecord(CaseRecord):
    stable_ready: bool
    latest_version_id: UUID | None
    latest_content: CaseContent | None
    updated_at: datetime | None


class CaseSearchPage(BaseModel):
    items: list[CaseListRecord]
    total: int
    page: int
    page_size: int


@app.get("/api/cases", response_model=CaseSearchPage)
def search_cases(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = None,
    code: str | None = None,
    purpose: str | None = None,
    target: str | None = None,
    module_id: UUID | None = None,
    app_id: UUID | None = None,
    level: str | None = None,
    status: str | None = None,
    android_version_id: UUID | None = None,
    app_version_id: UUID | None = None,
    who=Depends(subject),
):
    with db.engine.connect() as conn:
        return case_search.search(
            conn,
            page=page,
            page_size=page_size,
            q=q,
            code=code,
            purpose=purpose,
            target=target,
            module_id=module_id,
            app_id=app_id,
            level=level,
            status=status,
            android_version_id=android_version_id,
            app_version_id=app_version_id,
        )


@app.get("/api/catalogs", response_model=list[CatalogRecord])
def get_catalogs(who=Depends(subject)):
    with db.engine.connect() as conn:
        return catalogs.list_entries(conn)


@app.post("/api/catalogs", status_code=201, response_model=CatalogRecord)
def new_catalog(body: CatalogCreate, who=Depends(subject)):
    with db.engine.begin() as conn:
        return catalogs.save_entry(conn, body.model_dump(), who.id)


@app.put("/api/catalogs/{entry_id}", response_model=CatalogRecord)
def update_catalog(entry_id: UUID, body: CatalogWrite, who=Depends(subject)):
    with db.engine.begin() as conn:
        return catalogs.save_entry(conn, body.model_dump(), who.id, entry_id)


@app.delete("/api/catalogs/{entry_id}")
def remove_catalog(entry_id: UUID, who=Depends(subject)):
    with db.engine.begin() as conn:
        return catalogs.delete_entry(conn, entry_id, who.id)


class NodeRecord(BaseModel):
    id: str
    capabilities: list[str]
    last_seen: datetime | None
    connection_generation: int
    last_event_seq: int


class DeviceRecord(BaseModel):
    id: UUID
    node_id: str
    serial: str
    generation: int
    quarantined: bool
    attempt_id: UUID | None
    tags: list[str]
    adb_state: str | None = None
    adb_model: str | None = None
    adb_product: str | None = None
    observed_at: datetime | None = None
    artemis_state: str | None = None
    remote_held: bool = False


class AttemptRecord(BaseModel):
    id: UUID
    run_id: UUID
    number: int
    state: str
    version_id: UUID | None
    snapshot: CaseContent | None
    verdict: str
    cleanup: str
    evidence: str
    reason: str
    simulation: bool
    case_name: str
    created_at: datetime


class Overview(BaseModel):
    catalogs: list[CatalogRecord]
    cases: list[CaseRecord]
    versions: list[VersionRecord]
    nodes: list[NodeRecord]
    devices: list[DeviceRecord]
    attempts: list[AttemptRecord]


@app.get("/api/overview", response_model=Overview)
def overview(who=Depends(subject)):
    with db.engine.connect() as conn:
        return service.overview(conn)


@app.post("/api/nodes/adb-inventory")
def observe_device(body: AdbObservation):
    with db.engine.begin() as conn:
        node = db.one(
            conn,
            """INSERT INTO nodes(id,capabilities,last_seen)
            VALUES (:id,'["adb_inventory"]',now())
            ON CONFLICT(id) DO UPDATE SET last_seen=now()
            WHERE nodes.capabilities=excluded.capabilities RETURNING id""",
            id=body.node_id,
        )
        if not node:
            raise service.Conflict("node_role_conflict", "请为 ADB 采集使用独立的节点 ID")
        return dict(
            db.one(
                conn,
                """INSERT INTO devices(id,node_id,serial,tags,adb_state,adb_model,adb_product,observed_at,artemis_state)
            VALUES (:id,:node_id,:serial,'["adb"]',:state,:model,:product,now(),:artemis_state)
            ON CONFLICT(node_id,adb_endpoint,serial) DO UPDATE SET
            adb_state=excluded.adb_state,adb_model=COALESCE(excluded.adb_model,devices.adb_model),
            adb_product=COALESCE(excluded.adb_product,devices.adb_product),observed_at=now(),
            artemis_state=excluded.artemis_state
            RETURNING id,serial,adb_state,observed_at""",
                **body.model_dump(),
                id=uuid4(),
            )
        )


@app.post("/api/nodes/register")
def register(body: NodeRegistration):
    if len(set(body.device_serials)) != len(body.device_serials) or not all(
        s.startswith("SIM-") for s in body.device_serials
    ):
        raise HTTPException(422, "M0 only accepts unique SIM- device serials")
    with db.engine.begin() as conn:
        registered = db.one(
            conn,
            """INSERT INTO nodes(id,capabilities) VALUES (:id,CAST(:c AS jsonb))
            ON CONFLICT(id) DO UPDATE SET capabilities=excluded.capabilities
            WHERE NOT nodes.capabilities @> '["adb_inventory"]'::jsonb RETURNING id""",
            id=body.node_id,
            c=json.dumps(body.capabilities),
        )
        if not registered:
            raise service.Conflict("node_role_conflict", "此节点已用于 ADB 真机采集")
        for serial in body.device_serials:
            db.execute(
                conn,
                """INSERT INTO devices(id,node_id,serial,tags) VALUES (:id,:n,:s,CAST(:t AS jsonb))
                ON CONFLICT(node_id,adb_endpoint,serial) DO NOTHING""",
                id=uuid4(),
                n=body.node_id,
                s=serial,
                t=json.dumps(body.tags),
            )
    return {"protocol_version": 1, "node_id": body.node_id}


@app.post("/api/nodes/{node_id}/execution-model/{command_id}")
def node_execution_model(
    node_id: str,
    command_id: UUID,
):
    with db.engine.begin() as conn:
        model = model_configs.execution_model(conn, node_id, command_id)
    if model is None:
        raise HTTPException(404, "No active execution model authorization")
    return JSONResponse(model, headers={"Cache-Control": "no-store"})


@app.post("/api/nodes/{node_id}/runtime")
def open_node_runtime(node_id: str, body: OpenRuntime):
    with db.engine.begin() as conn:
        return node_runtime.open_runtime(conn, node_id, body.boot_id)


@app.get("/api/nodes/{node_id}/runtime")
def node_snapshot(node_id: str, x_node_generation: int = Header()):
    with db.engine.begin() as conn:
        return node_runtime.snapshot(conn, node_id, x_node_generation)


@app.post("/api/nodes/{node_id}/commands/{command_id}/claim")
def claim_node_command(node_id: str, command_id: UUID, x_node_generation: int = Header()):
    with db.engine.begin() as conn:
        return node_runtime.claim(conn, node_id, x_node_generation, command_id)


@app.post("/api/nodes/{node_id}/events")
def record_node_event(node_id: str, body: NodeUpdate, x_node_generation: int = Header()):
    with db.engine.begin() as conn:
        return node_runtime.update(conn, node_id, x_node_generation, body)


@app.websocket("/api/nodes/{node_id}/ws")
async def node_socket(ws: WebSocket, node_id: str):
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if hello.get("protocol_version") != 1 or hello.get("type") != "hello":
            await ws.send_json({"error": "unsupported_version"})
            await ws.close(code=1008)
            return
        last_seq = hello.get("last_seq")
        acked_seq = hello.get("acked_seq", 0)
        if (
            type(last_seq) is not int
            or type(acked_seq) is not int
            or not 0 <= acked_seq <= last_seq
        ):
            raise service.Conflict("invalid_message", "Invalid journal cursors")
        with db.engine.begin() as conn:
            node = db.one(conn, "SELECT * FROM nodes WHERE id=:id FOR UPDATE", id=node_id)
            if node and node["runtime_protocol"] == 2:
                raise service.Conflict(
                    "unsupported_version", "This node uses central runtime recovery"
                )
            if (
                node
                and "adb_inventory" in node["capabilities"]
                and not db.one(
                    conn,
                    "SELECT id FROM devices WHERE node_id=:n AND artemis_state IS NOT NULL LIMIT 1",
                    n=node_id,
                )
            ):
                raise service.Conflict("node_role_conflict", "采集节点不接收执行命令")
            if not node or last_seq < node["last_event_seq"]:
                raise service.Conflict("journal_loss", "Restore journal; do not replay device work")
            if acked_seq > node["last_event_seq"]:
                raise service.Conflict(
                    "control_cursor_rollback",
                    f"Control plane {node['last_event_seq']} < acknowledged {acked_seq}; reconcile before dispatch",
                )
            generation = node["connection_generation"] + 1
            db.execute(
                conn,
                "UPDATE nodes SET connection_generation=:g,last_seen=now() WHERE id=:id",
                g=generation,
                id=node_id,
            )
        await ws.send_json(
            {
                "type": "hello_ack",
                "connection_generation": generation,
                "ack_cursor": node["last_event_seq"],
            }
        )
        while True:
            with db.engine.connect() as conn:
                current = db.one(
                    conn, "SELECT connection_generation FROM nodes WHERE id=:id", id=node_id
                )
                if current["connection_generation"] != generation:
                    raise service.Conflict("stale_connection")
                commands = db.rows(
                    conn,
                    """SELECT payload FROM commands WHERE node_id=:n AND dispatch_ready
                    AND NOT accepted AND NOT retired ORDER BY created_at LIMIT 20""",
                    n=node_id,
                )
            for command in commands:
                await ws.send_json({"type": "command", "command": command["payload"]})
            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=1)
            except asyncio.TimeoutError:
                continue
            if message.get("type") == "ping":
                with db.engine.begin() as conn:
                    db.execute(
                        conn,
                        "UPDATE nodes SET last_seen=now() WHERE id=:id AND connection_generation=:g",
                        id=node_id,
                        g=generation,
                    )
                await ws.send_json({"type": "pong"})
            elif message.get("type") == "event":
                event = NodeEvent.model_validate(message["event"])
                with db.engine.begin() as conn:
                    cursor = service.ingest_event(conn, node_id, generation, event)
                await ws.send_json({"type": "event_ack", "seq": cursor})
            else:
                raise service.Conflict("invalid_message")
    except WebSocketDisconnect:
        pass
    except (service.Conflict, ValidationError, ValueError) as exc:
        await ws.send_json({"error": getattr(exc, "code", "invalid_message"), "detail": str(exc)})
        await ws.close(code=1008)


class RemoteLease(BaseModel):
    close: bool = False


class RemoteInput(BaseModel):
    kind: Literal["frame", "tap", "swipe", "text", "key"]
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    x2: float = Field(default=0, ge=0, le=1)
    y2: float = Field(default=0, ge=0, le=1)
    width: int = Field(default=0, ge=0, le=10000)
    height: int = Field(default=0, ge=0, le=10000)
    duration_ms: int = Field(default=350, ge=60, le=1500)
    hold_ms: int = Field(default=0, ge=0, le=2000)
    text: str = Field(default="", max_length=2000)
    key: Literal["HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN", "WAKEUP", "SLEEP"] = (
        "BACK"
    )


@app.post("/api/devices/{device_id}/remote/lease")
def remote_lease(
    device_id: UUID,
    body: RemoteLease,
    x_control_token: str | None = Header(default=None),
    who=Depends(subject),
):
    from control_plane.modules import remote

    with db.engine.begin() as conn:
        return remote.lease(conn, device_id, x_control_token, who.id, body.close)


@app.post("/api/devices/{device_id}/remote/actions")
def remote_action(
    device_id: UUID,
    body: RemoteInput,
    x_control_token: str | None = Header(default=None),
    who=Depends(subject),
):
    from control_plane.modules import remote

    with db.engine.begin() as conn:
        return remote.enqueue(conn, device_id, body.model_dump(), x_control_token, who.id)


@app.get("/api/devices/{device_id}/remote/actions/{action_id}")
def remote_result(device_id: UUID, action_id: UUID, who=Depends(subject)):
    with db.engine.connect() as conn:
        r = db.one(
            conn,
            "SELECT id,state,result FROM remote_actions WHERE id=:id AND device_id=:d",
            id=action_id,
            d=device_id,
        )
        if not r:
            raise HTTPException(404)
        return dict(r)


@app.get("/api/nodes/{node_id}/remote/{serial}")
def remote_poll(node_id: str, serial: str):
    with db.engine.begin() as conn:
        d = db.one(
            conn,
            "SELECT * FROM devices WHERE node_id=:n AND serial=:s FOR UPDATE",
            n=node_id,
            s=serial,
        )
        if not d:
            return None
        db.execute(
            conn,
            "DELETE FROM remote_actions WHERE created_at<now()-interval '2 minutes' AND device_id=:d",
            d=d["id"],
        )
        db.execute(
            conn,
            "UPDATE remote_actions SET state='failed',result='{\"error\":\"操作已过期\"}' WHERE device_id=:d AND state='queued' AND expires_at<=now()",
            d=d["id"],
        )
        db.execute(
            conn,
            "UPDATE remote_actions SET state='failed',result='{\"error\":\"节点中断，操作未重放\"}' WHERE device_id=:d AND state='executing' AND expires_at<now()-interval '20 seconds'",
            d=d["id"],
        )
        db.execute(
            conn,
            "UPDATE devices SET remote_token=NULL,remote_until=NULL WHERE id=:d AND remote_until<=now() AND artemis_state='ready' AND NOT EXISTS(SELECT 1 FROM remote_actions WHERE device_id=:d AND state='executing' AND payload->>'kind'<>'frame')",
            d=d["id"],
        )
        a = db.one(
            conn,
            "SELECT * FROM remote_actions WHERE device_id=:d AND state='queued' AND expires_at>now() ORDER BY (payload->>'kind'='frame'),created_at LIMIT 1 FOR UPDATE",
            d=d["id"],
        )
        if a:
            input = a["payload"]["kind"] != "frame"
            held = db.one(
                conn, "SELECT id FROM leases WHERE device_id=:d AND released_at IS NULL", d=d["id"]
            )
            authorized = db.one(
                conn,
                "SELECT id FROM devices WHERE id=:id AND remote_token=:t AND remote_until>now()",
                id=d["id"],
                t=a["token_hash"],
            )
            if input and (held or not authorized):
                db.execute(
                    conn,
                    "UPDATE remote_actions SET state='failed',result='{\"error\":\"设备被占用或控制权已失效\"}' WHERE id=:id",
                    id=a["id"],
                )
                return None
            db.execute(conn, "UPDATE remote_actions SET state='executing' WHERE id=:id", id=a["id"])
            return {"id": str(a["id"]), "payload": a["payload"]}
        db.execute(
            conn,
            "UPDATE remote_actions SET state='failed',result='{\"error\":\"节点中断，操作未重放\"}' WHERE device_id=:d AND state='executing' AND expires_at<now()-interval '20 seconds'",
            d=d["id"],
        )
        busy = db.one(
            conn,
            "SELECT id FROM remote_actions WHERE device_id=:d AND state='executing' LIMIT 1",
            d=d["id"],
        )
        if not busy and d["artemis_state"] == "ready":
            db.execute(
                conn,
                "UPDATE devices SET remote_token=NULL,remote_until=NULL WHERE id=:d AND remote_until<=now()",
                d=d["id"],
            )
        return None


class RemoteReceipt(BaseModel):
    result: dict


@app.post("/api/nodes/{node_id}/remote/{serial}/{action_id}")
def remote_receipt(
    node_id: str,
    serial: str,
    action_id: UUID,
    body: RemoteReceipt,
):
    if len(json.dumps(body.result)) > 10000000:
        raise HTTPException(413)
    with db.engine.begin() as conn:
        db.execute(
            conn,
            "UPDATE remote_actions a SET state=:s,result=CAST(:r AS jsonb) FROM devices d WHERE a.id=:id AND a.device_id=d.id AND d.node_id=:n AND d.serial=:sn AND a.state='executing'",
            id=action_id,
            n=node_id,
            sn=serial,
            s="failed" if body.result.get("error") else "done",
            r=json.dumps(body.result),
        )
    return {"ok": True}


@app.websocket("/api/devices/{device_id}/video")
async def video_view(ws: WebSocket, device_id: UUID):
    from control_plane.modules.video import channel

    subject()
    with db.engine.connect() as conn:
        device = db.one(
            conn, "SELECT id FROM devices WHERE id=:id AND adb_state IS NOT NULL", id=device_id
        )
    if not device:
        await ws.close(code=1008)
        return
    await ws.accept()
    media = channel(device_id)
    queue = asyncio.Queue(maxsize=64)
    cached = (media.config, *media.gop) if media.config else ()
    media.viewers.add(queue)
    sender = receiver = None
    try:
        await ws.send_json(media.status)
        for packet in cached:
            await ws.send_bytes(packet)
        await media.demand()

        async def send():
            while True:
                item = await queue.get()
                if isinstance(item, bytes):
                    await ws.send_bytes(item)
                else:
                    await ws.send_json(item)
                    if item.get("detail") == "播放端积压，请重新连接":
                        await ws.close(code=1013)
                        return

        async def receive():
            while True:
                await ws.receive_text()

        sender = asyncio.create_task(send())
        receiver = asyncio.create_task(receive())
        done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    finally:
        media.viewers.discard(queue)
        with anyio.CancelScope(shield=True):
            tasks_to_close = [task for task in (sender, receiver) if task is not None]
            for task in tasks_to_close:
                task.cancel()
            await asyncio.gather(*tasks_to_close, return_exceptions=True)
            try:
                await media.demand()
            except (RuntimeError, WebSocketDisconnect):
                pass


@app.websocket("/api/nodes/{node_id}/video/{serial}")
async def video_producer(ws: WebSocket, node_id: str, serial: str):
    from control_plane.modules.video import channel

    with db.engine.connect() as conn:
        device = db.one(
            conn,
            "SELECT id FROM devices WHERE node_id=:n AND serial=:s AND adb_state IS NOT NULL",
            n=node_id,
            s=serial,
        )
    if not device:
        await ws.close(code=1008)
        return
    await ws.accept()
    media = channel(device["id"])
    old = media.producer
    media.producer = ws
    if old:
        await old.close(code=1012)
    media.config = None
    media.gop = []
    media.gop_bytes = 0
    media.gop_valid = False
    media.status = {"state": "waiting", "detail": "等待启动视频"}
    media.publish(media.status)
    try:
        await media.demand()
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                packet = message["bytes"]
                if (
                    len(packet) < 12
                    or len(packet) > 2_000_000
                    or int.from_bytes(packet[8:12], "big") != len(packet) - 12
                ):
                    await ws.close(code=1008)
                    break
                media.packet(packet)
            elif message.get("text"):
                state = json.loads(message["text"])
                if state.get("state") not in ("starting", "streaming", "stopped", "error"):
                    await ws.close(code=1008)
                    break
                if state["state"] in ("starting", "stopped", "error"):
                    media.config = None
                    media.gop = []
                    media.gop_bytes = 0
                    media.gop_valid = False
                media.status = state
                media.publish(state)
    except WebSocketDisconnect:
        pass
    finally:
        if media.producer is ws:
            media.producer = None
            media.config = None
            media.gop = []
            media.gop_bytes = 0
            media.status = {"state": "error", "detail": "节点视频连接已中断"}
            media.publish(media.status)
