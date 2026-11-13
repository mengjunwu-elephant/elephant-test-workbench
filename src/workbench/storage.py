from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, String, Integer, Text, JSON, Boolean, event, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_home() -> Path:
    if os.getenv("ELEPHANT_WORKBENCH_DATA"):
        return Path(os.environ["ELEPHANT_WORKBENCH_DATA"]).expanduser().resolve()
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "ElephantTestWorkbench"


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="queued")
    resource: Mapped[str] = mapped_column(String)
    request: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    telemetry: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)


class DeviceLock(Base):
    __tablename__ = "device_locks"
    resource: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")


class Environment(Base):
    __tablename__ = "environments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    python: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String, default="preparing")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)


class HubUser(Base):
    __tablename__ = "hub_users"
    username: Mapped[str] = mapped_column(String, primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String)


class HubSession(Base):
    __tablename__ = "hub_sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String)
    expires_at: Mapped[str] = mapped_column(String)


class Release(Base):
    __tablename__ = "releases"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String)
    author: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ["runs", "environments", "wheelhouse", "backups", "exports"]:
            (self.root / name).mkdir(exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.root / 'workbench.sqlite3'}", connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(self.engine, "connect")
        def pragmas(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")

        from alembic import command
        from alembic.config import Config
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        cfg.set_main_option("sqlalchemy.url", str(self.engine.url).replace("%", "%%"))
        inspector = inspect(self.engine)
        existing = set(inspector.get_table_names())
        # 兼容开发初版 create_all 数据，仅在完整初始结构匹配时登记基线。
        if existing and "alembic_version" not in existing:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing or {c["name"] for c in inspector.get_columns(table.name)} != set(table.columns.keys()):
                    raise RuntimeError("发现未登记且不匹配的数据库结构；拒绝静默覆盖，请备份后迁移")
            command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.file_lock = threading.RLock()
        self.sequences: dict[str, int] = {}

    @contextmanager
    def session(self):
        with self.Session.begin() as s:
            yield s

    def get_setting(self, key: str, default: Any = None):
        with self.session() as s:
            row = s.get(Setting, key)
            return row.value if row else default

    def set_setting(self, key: str, value: Any):
        with self.session() as s:
            s.merge(Setting(key=key, value=value))

    def run_dir(self, task_id: str) -> Path:
        if not task_id.isalnum():
            raise ValueError("非法任务 ID")
        p = self.root / "runs" / task_id
        p.mkdir(exist_ok=True)
        return p

    def append_event(self, task_id: str, payload: dict) -> dict:
        with self.file_lock:
            p = self.run_dir(task_id) / "events.jsonl"
            if task_id not in self.sequences:
                previous = self.events(task_id, limit=1)
                self.sequences[task_id] = previous[-1]["seq"] if previous else 0
            self.sequences[task_id] += 1
            item = {**payload, "seq": self.sequences[task_id], "time": payload.get("time", now())}
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
            return item

    def events(self, task_id: str, after: int = 0, limit: int = 1000):
        from collections import deque
        p = self.run_dir(task_id) / "events.jsonl"
        if not p.exists():
            return []
        result = deque(maxlen=limit)
        with self.file_lock, p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if row["seq"] > after:
                        result.append(row)
                except (ValueError, KeyError):
                    continue
        return list(result)

