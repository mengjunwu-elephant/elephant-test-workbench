"""可独立部署的局域网用例服务。没有结果、日志或设备控制上传接口。"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import HubLogin, PublishRequest, CaseDocument
from .storage import Store, HubUser, HubSession, Release, now


def digest_document(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("密码至少 10 个字符")
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + result.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt, expected = encoded.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except ValueError:
        return False


def add_user(store: Store, username: str, password: str, role: str):
    if not username or len(username) > 80 or role not in ("tester", "maintainer"):
        raise ValueError("用户名或角色不合法")
    with store.session() as s:
        if s.get(HubUser, username):
            raise ValueError("用户名已存在")
        s.add(HubUser(username=username, password_hash=hash_password(password), role=role))


class NewUser(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10)
    role: str = "tester"


def create_hub(root: Path) -> FastAPI:
    app = FastAPI(title="Elephant LAN Case Library", docs_url="/docs")
    store = Store(root)
    app.state.store = store

    def authorize(authorization: str | None, publish=False):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "请先登录用例服务")
        token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with store.session() as s:
            session = s.get(HubSession, token_hash)
            if not session or session.expires_at <= now():
                raise HTTPException(401, "登录已失效")
            user = s.get(HubUser, session.username)
            if not user:
                raise HTTPException(401, "账号不存在")
            if publish and user.role != "maintainer":
                raise HTTPException(403, "仅维护人员可以发布正式用例")
            return {"username": user.username, "role": user.role}

    @app.get("/health")
    def health():
        return {"ok": True, "service": "case-library", "protocol": 1, "results_upload": False}

    @app.post("/auth/login")
    def login(body: HubLogin):
        with store.session() as s:
            user = s.get(HubUser, body.username)
            if not user or not verify_password(body.password, user.password_hash):
                raise HTTPException(401, "用户名或密码错误")
            token = secrets.token_urlsafe(40)
            expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            s.add(HubSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), username=user.username, expires_at=expires))
            return {"token": token, "username": user.username, "role": user.role, "expires_at": expires}

    @app.post("/auth/logout")
    def logout(authorization: str | None = Header(default=None)):
        authorize(authorization)
        with store.session() as s:
            session = s.get(HubSession, hashlib.sha256(authorization[7:].encode()).hexdigest())
            if session:
                s.delete(session)
        return {"ok": True}

    @app.post("/users")
    def new_user(body: NewUser, authorization: str | None = Header(default=None)):
        authorize(authorization, publish=True)
        try:
            add_user(store, body.username, body.password, body.role)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"username": body.username, "role": body.role}

    @app.get("/releases")
    def list_releases(authorization: str | None = Header(default=None)):
        authorize(authorization)
        with store.session() as s:
            rows = s.scalars(select(Release).order_by(Release.case_id, Release.revision.desc())).all()
            newest = {}
            for row in rows:
                if row.case_id not in newest:
                    newest[row.case_id] = {"case_id": row.case_id, "revision": row.revision, "name": row.document["name"], "model": row.document["model"], "digest": row.digest, "author": row.author, "created_at": row.created_at}
            return list(newest.values())

    @app.get("/releases/{case_id}/{revision}")
    def get_release(case_id: str, revision: int, authorization: str | None = Header(default=None)):
        authorize(authorization)
        with store.session() as s:
            row = s.get(Release, f"{case_id}:{revision}")
            if not row:
                raise HTTPException(404, "版本不存在")
            return {"document": row.document, "digest": row.digest, "author": row.author, "created_at": row.created_at}

    @app.post("/releases", status_code=201)
    def publish(body: PublishRequest, authorization: str | None = Header(default=None)):
        user = authorize(authorization, publish=True)
        doc = body.document.model_dump()
        case_id = doc["shared_id"] or doc["id"]
        try:
            with store.session() as s:
                latest = s.scalars(select(Release).where(Release.case_id == case_id).order_by(Release.revision.desc())).first()
                revision = latest.revision if latest else 0
                if body.base_revision != revision:
                    raise HTTPException(409, f"共享版本已变化（当前 {revision}），请先检查差异再发布")
                revision += 1
                doc.update(id=case_id, shared_id=case_id, status="shared", revision=revision, base_shared_revision=revision)
                CaseDocument.model_validate(doc)
                digest = digest_document(doc)
                s.add(Release(key=f"{case_id}:{revision}", case_id=case_id, revision=revision, document=doc, digest=digest, author=user["username"]))
                s.flush()
                return {"case_id": case_id, "revision": revision, "digest": digest}
        except IntegrityError as exc:
            raise HTTPException(409, "并发发布冲突，请重新检查共享版本") from exc

    return app
