from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import venv
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from sqlalchemy import select
from fastapi import HTTPException

from .models import uid
from .storage import Store, Environment

BASE_REQUIREMENTS = ["pyserial>=3.5,<4", "numpy>=1.24,<3"]
BUNDLED_WHEELS = Path(__file__).with_name("bundled_wheels")


def creation_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def python_in(directory: Path):
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def command(command, timeout=120, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **creation_options(), **kwargs)


def probe(python: str):
    code = "import importlib.metadata as m,json,sys,platform,pymycobot; print(json.dumps({'python':sys.version,'executable':sys.executable,'system':platform.system(),'machine':platform.machine(),'sdk_version':m.version('pymycobot'),'dependencies':{d.metadata['Name']:d.version for d in m.distributions()}}))"
    result = command([python, "-c", code], timeout=20)
    if result.returncode:
        raise ValueError("环境探测失败：" + result.stderr[-2000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def install_failure(log: str):
    """Explain pip's actual failure without replacing or suppressing its raw log."""
    missing = re.search(r"Could not find a version that satisfies the requirement (.+?)(?: \(from |$)", log, re.MULTILINE)
    unsupported = re.search(r"([^\s]+\.whl) is not a supported wheel on this platform", log)
    if missing:
        requirement = missing.group(1).strip()
        try:
            base_name = Requirement(requirement).name.lower()
        except ValueError:
            base_name = ""
        if base_name in {"pyserial", "numpy"}:
            return {"error_code": "missing_base_dependency", "requirement": requirement,
                    "error": f"应用基础依赖 {base_name} 未准备成功。新版会自动安装此依赖，无需单独上传；请使用更新后的应用重新导入 SDK，仍失败时修复应用安装。现用环境未修改。"}
        return {"error_code": "missing_offline_distribution", "requirement": requirement,
                "error": f"离线包不完整或没有匹配版本：找不到 {requirement}。请提供匹配当前 Python/系统的依赖 wheel，与 pymycobot 一起重新上传；现用环境未修改。"}
    if unsupported:
        return {"error_code": "incompatible_wheel", "error": f"wheel 不兼容当前 Python/系统：{unsupported.group(1)}。请更换匹配的 wheel；现用环境未修改。"}
    errors = [line.strip().removeprefix("ERROR:").strip() for line in log.splitlines() if line.strip().startswith("ERROR:")]
    reason = "；".join(errors[-3:])[:1800] or "安装进程未成功退出，请展开安装记录查看原始日志"
    return {"error_code": "dependency_conflict" if "ResolutionImpossible" in log else "offline_install_failed",
            "error": "离线安装失败：" + reason + "；现用环境未修改。"}


class EnvironmentManager:
    def __init__(self, store: Store):
        self.store = store

    def list(self):
        with self.store.session() as s:
            result = []
            for row in s.scalars(select(Environment).order_by(Environment.created_at.desc())):
                item = {c.name: getattr(row, c.name) for c in Environment.__table__.columns}
                details = dict(item["details"])
                # Existing failed imports also receive a precise display; stored evidence stays intact.
                if row.state == "failed" and not details.get("error_code") and "Could not find a version that satisfies the requirement" in details.get("log", ""):
                    details.update(install_failure(details["log"]))
                item["details"] = details
                result.append(item)
            return result

    def create(self, uploads: list[tuple[str, bytes]]):
        if not uploads:
            raise HTTPException(400, "请选择 wheel 或包含 wheel 和 manifest.json 的 zip")
        environment_id = uid()
        bundle = self.store.root / "wheelhouse" / environment_id
        bundle.mkdir()
        total = 0
        for name, data in uploads:
            filename = Path(name.replace("\\", "/")).name
            if not filename.lower().endswith((".whl", ".zip", ".json")):
                raise HTTPException(400, "仅支持 .whl、.zip 和 manifest.json")
            total += len(data)
            if total > 350 * 1024 * 1024:
                raise HTTPException(413, "离线包总大小超过 350 MB")
            if filename.lower().endswith(".zip"):
                import io
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        unpacked = sum(x.file_size for x in archive.infolist())
                        if unpacked > 1024 * 1024 * 1024 or len(archive.infolist()) > 3000:
                            raise HTTPException(400, "压缩包体积或文件数量超限")
                        for member in archive.infolist():
                            if member.is_dir():
                                continue
                            path = Path(member.filename.replace("\\", "/"))
                            if path.is_absolute() or ".." in path.parts or ":" in member.filename:
                                raise HTTPException(400, "压缩包路径不安全")
                            if path.suffix not in (".whl", ".json"):
                                raise HTTPException(400, "压缩包只允许 wheel 与 JSON 清单")
                            dest = bundle / path.name
                            if dest.exists():
                                raise HTTPException(400, "离线包中存在重复文件名")
                            dest.write_bytes(archive.read(member))
                except zipfile.BadZipFile as exc:
                    raise HTTPException(400, "无效 zip 文件") from exc
            else:
                dest = bundle / filename
                if dest.exists():
                    raise HTTPException(400, "重复文件名")
                dest.write_bytes(data)
        wheels = list(bundle.glob("*.whl"))
        if not wheels:
            raise HTTPException(400, "包内没有 wheel")
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wheels}
        from email.parser import BytesParser
        for wheel in wheels:
            try:
                with zipfile.ZipFile(wheel) as z:
                    metadata_names = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
                    if len(metadata_names) != 1:
                        raise ValueError("wheel 缺少唯一的元数据")
                    metadata = BytesParser().parsebytes(z.read(metadata_names[0]))
                    for dependency in metadata.get_all("Requires-Dist", []):
                        if Requirement(dependency).url:
                            raise ValueError("拒绝含 URL 依赖的 wheel，离线准备不能拉取外部文件")
            except (zipfile.BadZipFile, ValueError) as exc:
                raise HTTPException(400, f"wheel 元数据无效：{wheel.name}: {exc}") from exc
        manifest_file = bundle / "manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}
        if not isinstance(manifest, dict) or not isinstance(manifest.get("sha256", {}), dict):
            raise HTTPException(400, "清单和 sha256 字段必须是对象")
        for name, expected in manifest.get("sha256", {}).items():
            if hashes.get(name) != expected:
                raise HTTPException(400, f"校验失败：{name}")
        requirements = manifest.get("requirements", [])
        if not isinstance(requirements, list) or not all(isinstance(x, str) for x in requirements):
            raise HTTPException(400, "requirements 必须为字符串数组")
        if not requirements:
            from packaging.utils import parse_wheel_filename
            sdk_wheels = [(p, parse_wheel_filename(p.name)) for p in wheels if p.name.lower().startswith("pymycobot-")]
            if len(sdk_wheels) != 1:
                raise HTTPException(400, "无清单时需要且仅允许一个 pymycobot wheel")
            requirements = [f"pymycobot=={sdk_wheels[0][1][1]}"]
        try:
            parsed = [Requirement(x) for x in requirements]
            if any(p.url for p in parsed) or not any(p.name.lower() == "pymycobot" for p in parsed):
                raise ValueError("仅接受本地离线依赖，必须包含 pymycobot")
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "离线依赖清单不合法："+str(exc)) from exc
        bundled_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in BUNDLED_WHEELS.glob("*.whl")}
        details = {"hashes": hashes, "requirements": requirements, "base_requirements": BASE_REQUIREMENTS, "bundled_hashes": bundled_hashes, "source": "offline-wheel", "system": platform.system(), "machine": platform.machine(), "progress": "正在创建独立环境并安装基础依赖", "log": ""}
        with self.store.session() as s:
            s.add(Environment(id=environment_id, name=manifest.get("name", "离线 SDK 环境"), details=details, state="preparing", active=False))
        threading.Thread(target=self._prepare, args=(environment_id, bundle, details), daemon=True).start()
        return {"id": environment_id, "state": "preparing"}

    def _prepare(self, environment_id, bundle, details):
        directory = self.store.root / "environments" / environment_id
        try:
            venv.EnvBuilder(with_pip=True).create(directory)
            python = str(python_in(directory))
            result = command([python, "-m", "pip", "--isolated", "install", "--no-index", "--only-binary=:all:", "--find-links", str(bundle), "--find-links", str(BUNDLED_WHEELS), *BASE_REQUIREMENTS, *details["requirements"]], timeout=600, env={**os.environ, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
            details["log"] = (result.stdout + "\n" + result.stderr)[-20000:]
            if result.returncode:
                details.update(install_failure(details["log"]))
                raise ValueError(details["error"])
            check = command([python, "-m", "pip", "check"], timeout=60)
            details["log"] += "\n" + check.stdout + check.stderr
            if check.returncode:
                raise ValueError("依赖校验失败")
            details.update(probe(python))
            details["progress"] = "已准备，可在空闲时启用；尚未验证实机兼容性"
            with self.store.session() as s:
                row = s.get(Environment, environment_id)
                row.python, row.details, row.state = python, details, "ready"
        except Exception as exc:
            details["error"] = str(exc)
            details["progress"] = "准备失败，原环境不变"
            with self.store.session() as s:
                row = s.get(Environment, environment_id)
                row.details, row.state = details, "failed"

    def activate(self, environment_id: str, has_running):
        if has_running():
            raise HTTPException(409, "任务执行中，空闲后才能切换 SDK 环境")
        with self.store.session() as s:
            row = s.get(Environment, environment_id)
            if not row or row.state != "ready":
                raise HTTPException(400, "只能启用准备完成的环境")
            for item in s.scalars(select(Environment)):
                item.active = item.id == environment_id
        return {"ok": True, "active": environment_id}
