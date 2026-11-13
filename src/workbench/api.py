from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic import ValidationError
from sqlalchemy import select, update

from . import __version__
from .catalog import MODELS, METHODS
from .environments import EnvironmentManager, creation_options
from .models import CaseDocument, RunRequest, HubLogin, PublishRequest, PlanDocument, uid
from .sharing import Sharing
from .storage import Store, Case, Plan, Task, DeviceLock, Environment, data_home, now
from .tasks import TaskManager, TERMINAL
from .instance import InstanceLock
from .case_repository import CaseRepository
from .plans import save_plan


def serialize(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def create_app(root: Path | None = None) -> FastAPI:
    directory = (root or data_home()).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    instance_lock = InstanceLock(directory)
    try:
        store = Store(directory)
        from .upgrade import archive_obsolete_preview
        archive_obsolete_preview(store)
    except BaseException:
        instance_lock.close()
        raise
    manager, environments, sharing = TaskManager(store), EnvironmentManager(store), Sharing(store)
    repository = CaseRepository(store, manager)
    if root is None:
        repository.load(startup=True)
    stop_checker = threading.Event()

    def checker():
        while not stop_checker.wait(60):
            if sharing.token:
                try:
                    sharing.check()
                except Exception as exc:
                    status = store.get_setting("shared_updates", {"releases": []})
                    store.set_setting("shared_updates", {**status, "error": str(getattr(exc, "detail", exc)), "checked_at": now()})

    @asynccontextmanager
    async def lifespan(app):
        thread = threading.Thread(target=checker, daemon=True)
        thread.start()
        yield
        stop_checker.set()
        manager.shutdown()
        thread.join(timeout=1)
        instance_lock.close()

    app = FastAPI(title="Elephant Test Workbench", version=__version__, lifespan=lifespan)
    app.state.store, app.state.manager, app.state.sharing, app.state.repository = store, manager, sharing, repository

    @app.exception_handler(ValidationError)
    async def internal_validation_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)
    @app.middleware("http")
    async def protect_local(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {str(request.base_url).rstrip("/"), "http://localhost:5173", "http://127.0.0.1:5173"}
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in allowed:
            return JSONResponse({"detail": "拒绝来自其他网页的本机控制请求"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "data_directory": str(store.root), "python": sys.version, "automatic_hardware_connection": False}

    @app.get("/api/models")
    def models():
        return MODELS

    @app.get("/api/methods")
    def methods():
        return METHODS

    @app.get("/api/settings")
    def settings():
        return {"connection": store.get_setting("connection", {"mode": "hardware", "model": "ultraarm_p1", "address": "", "baudrate": 115200}), "hub": {**store.get_setting("hub", {}), "user": sharing.user}, "preferences": store.get_setting("preferences", {"telemetry_stale_seconds": 2, "retention_days": 90})}

    @app.put("/api/settings/{key}")
    def save_setting(key: str, body: dict):
        if key not in {"connection", "hub", "preferences"}:
            raise HTTPException(400, "未知设置")
        if key == "hub":
            sharing.set_url(body.get("url", ""))
        else:
            if key == "connection":
                from .models import Connection
                body = Connection.model_validate(body).model_dump()
            store.set_setting(key, body)
        return {"ok": True}

    @app.get("/api/apis")
    def apis(model: str = "ultraarm_p1", mode: Literal["hardware"] = "hardware", environment_id: str = ""):
        if model not in {x["id"] for x in MODELS}:
            raise HTTPException(400, "未知机型")
        python, _ = manager.resolve_environment(RunRequest(connection={"mode": "hardware", "model": model, "environment_id": environment_id}))
        command = [python, "-m", "workbench.worker", "--describe", model]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}, **creation_options())
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise HTTPException(400, "SDK 接口目录读取失败；当前版本可能不支持此机型："+result.stderr[-1500:]) from exc

    @app.get("/api/cases")
    def list_cases(model: str = ""):
        with store.session() as s:
            return [row.document for row in s.scalars(select(Case).order_by(Case.updated_at.desc())) if not model or row.document["model"] == model]

    @app.get('/api/case-repository')
    def repository_status():
        return repository.status()

    @app.post('/api/case-repository/load')
    def load_repository():
        return repository.load()

    @app.post('/api/case-repository/save')
    def save_repository():
        return repository.save()

    @app.post('/api/case-repository/git')
    def git_repository(body: dict):
        return repository.action(body.get('action'), body.get('message', ''))

    @app.get('/api/case-repository/source/{case_id}')
    def migration_source(case_id: str):
        return repository.source(case_id)

    @app.get('/api/plans')
    def list_plans():
        with store.session() as s:
            return [p.document for p in s.scalars(select(Plan).order_by(Plan.updated_at.desc()))]

    @app.post('/api/plans',status_code=201)
    def new_plan(body: PlanDocument):
        with manager.gate:
            return save_plan(store,body,creating=True)

    @app.put('/api/plans/{plan_id}')
    def update_plan(plan_id: str,body: PlanDocument):
        if body.id!=plan_id:raise HTTPException(409,'计划ID不一致')
        with manager.gate:
            return save_plan(store,body)

    @app.post('/api/plans/{plan_id}/fork',status_code=201)
    def fork_plan(plan_id: str):
        with manager.gate,store.session() as s:
            row=s.get(Plan,plan_id)
            if not row:raise HTTPException(404,'计划不存在')
            doc=copy.deepcopy(row.document)
            doc.update(id=uid(),name=(doc['name']+' · 副本')[:200],revision=1,archived=False)
            for item in doc['items']:item['item_id']=uid()
            s.add(Plan(id=doc['id'],document=doc))
            return doc

    @app.post('/api/tasks/preview')
    def preview_task(body: RunRequest):
        try:
            return {'ready':True,**manager.preview(body)}
        except HTTPException as exc:
            return {'ready':False,'errors':[exc.detail]}

    @app.get('/api/remediation')
    def remediation():
        path=repository.library/'remediation-ledger.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'status':'not_generated','entries':[]}

    @app.post("/api/cases", status_code=201)
    def new_case(body: CaseDocument):
        if body.status != "draft":
            raise HTTPException(400, "新用例先保存为个人草稿")
        with store.session() as s:
            if s.get(Case, body.id):
                raise HTTPException(409, "用例 ID 已存在")
            s.add(Case(id=body.id, document=body.model_dump()))
        return body

    @app.put("/api/cases/{case_id}")
    def update_case(case_id: str, body: CaseDocument):
        if body.id != case_id:
            raise HTTPException(409, "用例 ID 不一致")
        document = body.model_dump()
        document.update(revision=body.revision+1, status="draft")
        with store.session() as s:
            result = s.execute(update(Case).where(Case.id == case_id, Case.document["status"].as_string() == "draft", Case.document["revision"].as_integer() == body.revision).values(document=document, updated_at=now()))
            if result.rowcount != 1:
                row = s.get(Case, case_id)
                if not row:
                    raise HTTPException(404, "用例不存在")
                if row.document["status"] == "shared":
                    raise HTTPException(403, "正式用例只读，请复制为草稿后编辑")
                raise HTTPException(409, "用例版本已变化，请重新加载")
            return document

    @app.post("/api/cases/{case_id}/fork")
    def fork_case(case_id: str):
        with store.session() as s:
            row = s.get(Case, case_id)
            if not row:
                raise HTTPException(404, "用例不存在")
            document = {**row.document, "id": uid(), "name": row.document["name"]+" · 副本", "status": "draft"}
            s.add(Case(id=document["id"], document=document))
            return document

    @app.get("/api/cases-export")
    def export_cases():
        return {"format": "elephant-cases-v1", "documents": list_cases()}

    @app.post("/api/cases-import")
    def import_cases(body: dict):
        raw = body.get("documents", [body])
        if not isinstance(raw, list) or len(raw) > 1000:
            raise HTTPException(400, "最多导入 1000 条用例")
        documents = [CaseDocument.model_validate(d).model_dump() for d in raw]
        with store.session() as s:
            for document in documents:
                document.update(id=uid(), status="draft")
                s.add(Case(id=document["id"], document=document))
        return {"imported": len(documents)}

    @app.get("/api/tasks")
    def tasks():
        with store.session() as s:
            recent = list(s.scalars(select(Task).order_by(Task.created_at.desc()).limit(200)))
            active = list(s.scalars(select(Task).where(Task.state.not_in(TERMINAL))))
            rows = {t.id:t for t in recent+active}
            return [serialize(t) for t in sorted(rows.values(), key=lambda t:t.created_at, reverse=True)]

    @app.post("/api/tasks", status_code=201)
    def start_task(body: RunRequest):
        return {"id": manager.start(body)}

    def get_task(task_id):
        with store.session() as s:
            row = s.get(Task, task_id)
            if not row:
                raise HTTPException(404, "任务不存在")
            return serialize(row)

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        return get_task(task_id)

    @app.get("/api/tasks/{task_id}/events")
    def events(task_id: str, after: int = 0):
        get_task(task_id)
        return store.events(task_id, after=max(0, after))

    @app.post("/api/tasks/{task_id}/control")
    def control(task_id: str, body: dict):
        manager.control(task_id, body.get("action", ""), body.get("id", ""), body.get("answer", ""))
        return {"ok": True}

    @app.websocket("/api/tasks/{task_id}/stream")
    async def stream(websocket: WebSocket, task_id: str):
        origin = websocket.headers.get("origin", "")
        host = websocket.headers.get("host", "")
        if origin and origin not in {f"http://{host}", f"https://{host}", "http://127.0.0.1:5173", "http://localhost:5173"}:
            await websocket.close(code=1008)
            return
        try:
            get_task(task_id)
        except HTTPException:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        # 每个连接维护文件读取位置，长期老化不会反复扫描整个日志。
        offset = 0
        path = store.run_dir(task_id) / "events.jsonl"
        try:
            while True:
                if path.exists():
                    with store.file_lock, path.open("rb") as f:
                        f.seek(offset)
                        lines = f.readlines(512*1024)
                        offset = f.tell()
                    for line in lines:
                        try:
                            await websocket.send_json(json.loads(line))
                        except ValueError:
                            pass
                await websocket.send_json({"type": "heartbeat", "time": now()})
                await asyncio.sleep(0.3)
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.get("/api/tasks/{task_id}/export")
    def export_task(task_id: str):
        row = get_task(task_id)
        run_dir = store.run_dir(task_id)
        archive = store.root / "exports" / f"{task_id}.zip"
        with store.file_lock, zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("task.json", json.dumps(row, ensure_ascii=False, indent=2))
            for p in run_dir.rglob("*"):
                if p.is_file():
                    z.write(p, str(p.relative_to(run_dir)))
        return FileResponse(archive, filename=archive.name)

    @app.get("/api/locks")
    def locks():
        with store.session() as s:
            return [serialize(row) for row in s.scalars(select(DeviceLock))]

    @app.get("/api/environments")
    def list_environments():
        return environments.list()

    @app.post("/api/environments/import", status_code=202)
    async def import_environment(files: list[UploadFile] = File(...)):
        uploads, total = [], 0
        for file in files:
            content = await file.read(350*1024*1024+1)
            total += len(content)
            if total > 350*1024*1024:
                raise HTTPException(413, "离线包总大小超过 350 MB")
            uploads.append((file.filename or "", content))
        try:
            return environments.create(uploads)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/environments/{environment_id}/activate")
    def activate_environment(environment_id: str):
        with manager.gate:
            return environments.activate(environment_id, manager.has_running)

    @app.post("/api/shared/login")
    def shared_login(body: HubLogin):
        return sharing.login(body.username, body.password)

    @app.post("/api/shared/logout")
    def shared_logout():
        if sharing.token:
            try:
                sharing.request("POST", "/auth/logout")
            finally:
                sharing.token, sharing.user = "", {}
        return {"ok": True}

    @app.get("/api/shared/status")
    def shared_status():
        return {**store.get_setting("shared_updates", {"releases": []}), "user": sharing.user, "url": sharing.base()}

    @app.post("/api/shared/check")
    def shared_check():
        return sharing.check()

    @app.post("/api/shared/enable")
    def enable_shared(body: dict):
        return sharing.enable(str(body["case_id"]), int(body["revision"]))

    @app.post("/api/shared/publish")
    def publish_shared(body: PublishRequest):
        result = sharing.request("POST", "/releases", json=body.model_dump())
        with store.session() as s:
            row = s.get(Case, body.document.id)
            if row and row.document.get("status") == "draft":
                row.document = {**row.document, "shared_id": result["case_id"], "base_shared_revision": result["revision"]}
        return result

    @app.post("/api/shared/users")
    def create_shared_user(body: dict):
        return sharing.request("POST", "/users", json=body)

    @app.post("/api/backups")
    def backup():
        backup_id = uid()
        db_file = store.root / "backups" / f"{backup_id}.sqlite3"
        archive = store.root / "backups" / f"{backup_id}.zip"
        # 备份期间阻止新任务，保证数据库快照与不可变任务文件边界一致。
        with manager.gate:
            if manager.has_running():
                raise HTTPException(409, "完整备份需等待任务空闲，单次任务可单独导出")
            with sqlite3.connect(store.root / "workbench.sqlite3") as source, sqlite3.connect(db_file) as target:
                source.backup(target)
            with store.file_lock, zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(db_file, "workbench.sqlite3")
                for folder in ("runs", "archives"):
                    for p in (store.root / folder).rglob("*"):
                        if p.is_file():
                            z.write(p, str(p.relative_to(store.root)))
                z.writestr("manifest.json", json.dumps({"format": "elephant-backup-v1", "created_at": now(), "platform": __version__, "sdk_environments_included": False}))
        return {"id": backup_id, "download": f"/api/backups/{backup_id}"}

    @app.get("/api/backups/{backup_id}")
    def download_backup(backup_id: str):
        if not backup_id.isalnum():
            raise HTTPException(400, "非法备份 ID")
        path = store.root / "backups" / (backup_id + ".zip")
        if not path.is_file():
            raise HTTPException(404, "备份不存在")
        return FileResponse(path, filename=path.name)

    web = Path(__file__).parent / "web"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
