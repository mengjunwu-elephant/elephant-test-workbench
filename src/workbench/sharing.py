from __future__ import annotations

import threading
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from .hub import digest_document
from .models import CaseDocument
from .storage import Store, Case, now


class Sharing:
    def __init__(self, store: Store):
        self.store = store
        self.token = ""
        self.user: dict = {}
        self.lock = threading.RLock()

    def base(self):
        return self.store.get_setting("hub", {}).get("url", "").rstrip("/")

    def set_url(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(400, "请输入无账号、查询或片段的 http(s) 用例服务地址")
        self.store.set_setting("hub", {"url": url.rstrip("/")})
        self.token, self.user = "", {}

    def request(self, method, path, **kwargs):
        if not self.base():
            raise HTTPException(400, "请配置局域网用例服务地址")
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                response = client.request(method, self.base()+path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise HTTPException(502, "无法连接用例服务：" + str(exc)) from exc
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.text[:300])
            except ValueError:
                detail = response.text[:300]
            raise HTTPException(response.status_code, detail)
        return response.json()

    def login(self, username, password):
        result = self.request("POST", "/auth/login", json={"username": username, "password": password})
        self.token = result.pop("token")
        self.user = result
        return result

    def check(self):
        with self.lock:
            releases = self.request("GET", "/releases")
            status = {"checked_at": now(), "releases": releases, "error": ""}
            self.store.set_setting("shared_updates", status)
            return status

    def enable(self, case_id: str, revision: int):
        payload = self.request("GET", f"/releases/{case_id}/{revision}")
        document = payload["document"]
        if digest_document(document) != payload["digest"]:
            raise HTTPException(400, "共享用例校验不一致")
        doc = CaseDocument.model_validate(document)
        if doc.id != case_id or doc.revision != revision or doc.status != "shared":
            raise HTTPException(400, "版本标识不一致")
        # 共享缓存独立 ID，永不覆盖个人草稿；任务已冻结 document 快照。
        cache_id = "shared-" + case_id
        cached = doc.model_dump()
        cached.update(id=cache_id, shared_id=case_id)
        with self.store.session() as s:
            row = s.get(Case, cache_id)
            if row and row.document.get("revision", 0) > revision:
                raise HTTPException(409, "不能通过更新入口覆盖为更旧版本；如需旧版请复制成草稿")
            s.merge(Case(id=cache_id, document=cached, updated_at=now()))
        return cached
