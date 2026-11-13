"""本项目内的可审查用例文件。Git 不同步设备配置、SDK 或结果。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading

from fastapi import HTTPException
from sqlalchemy import select

from .models import CaseDocument
from .storage import Case, now


def digest(document):
    normalized = CaseDocument.model_validate(document).model_dump()
    # 仅规范 schema 声明的浮点字段，参数/预期值的 JSON 类型保持不变。
    for step in normalized['steps']:
        step['timeout'] = float(step['timeout'])
        step['tolerance'] = float(step['tolerance'])
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class CaseRepository:
    def __init__(self, store, manager, library=None, checkout=None):
        self.store, self.manager = store, manager
        self.library = Path(library or Path(__file__).with_name('case_library')).resolve()
        project = Path(__file__).resolve().parents[2]
        self.checkout = Path(checkout).resolve() if checkout else project if (project / '.git').is_dir() else None
        self.gate = threading.RLock()

    def files(self):
        documents = {}
        for path in sorted(self.library.glob('cases/*/*.json')):
            if not path.resolve().is_relative_to(self.library):
                raise HTTPException(400, '拒绝用例目录外的文件链接')
            doc = CaseDocument.model_validate_json(path.read_text(encoding='utf-8')).model_dump()
            if doc['status'] != 'draft' or doc['id'] in documents:
                raise HTTPException(409, '用例文件必须为草稿且 ID 唯一')
            documents[doc['id']] = (doc, path)
        return documents

    def load(self, startup=False):
        with self.gate, self.manager.gate:
            documents = self.files()
            baseline = self.store.get_setting('case_repository_baseline', {})
            conflicts, updates, deleted = [], [], []
            with self.store.session() as s:
                for key, (doc, _) in documents.items():
                    row = s.get(Case, key)
                    if row and digest(row.document) != digest(doc):
                        if startup:
                            continue  # 启动不覆盖本机编辑；显式同步时报告冲突。
                        if key not in baseline or digest(row.document) != baseline[key]:
                            conflicts.append(key)
                            continue
                    updates.append(doc)
                if not startup:
                    for key in baseline.keys() - documents.keys():
                        row = s.get(Case, key)
                        if row and digest(row.document) != baseline[key]:
                            conflicts.append(key)
                        elif row:
                            deleted.append(row)
                if conflicts:
                    raise HTTPException(409, {'message':'存在本机未写回编辑，请先处理差异，未覆盖任何用例', 'case_ids':conflicts})
                for doc in updates:
                    s.merge(Case(id=doc['id'], document=doc, updated_at=now()))
                    baseline[doc['id']] = digest(doc)
                for row in deleted:
                    s.delete(row)
                    baseline.pop(row.id, None)
            self.store.set_setting('case_repository_baseline', baseline)
            return {'loaded': len(updates), 'deleted': len(deleted), 'file_count':len(documents)}

    def writable(self):
        if not self.checkout or not self.library.is_relative_to(self.checkout):
            raise HTTPException(400, '文件写回和 Git 操作需使用本项目源码 checkout；发行包可读取随包用例或导出 JSON')

    def save(self):
        with self.gate, self.manager.gate:
            self.writable()
            disk = self.files()
            baseline = self.store.get_setting('case_repository_baseline', {})
            pending = []
            with self.store.session() as s:
                docs = [CaseDocument.model_validate(row.document).model_dump() for row in s.scalars(select(Case)) if row.document.get('status') == 'draft']
            for doc in docs:
                key = doc['id']
                previous = disk.get(key)
                if not previous and key in baseline:
                    raise HTTPException(409, '磁盘用例已被删除，请先载入并处理差异：'+key)
                if previous and digest(previous[0]) != digest(doc) and (key not in baseline or digest(previous[0]) != baseline[key]):
                    raise HTTPException(409, '磁盘用例已变化，拒绝覆盖：'+key)
                # 文件名只使用经模型校验的 ID；机型不用于构造写入路径。
                path = previous[1] if previous else self.library / 'cases' / 'local' / (key+'.json')
                if not path.resolve().is_relative_to(self.library):
                    raise HTTPException(400, '用例文件路径不安全')
                pending.append((doc, path))
            for doc, path in pending:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf-8')
                temporary.replace(path)
                baseline[doc['id']] = digest(doc)
            self.store.set_setting('case_repository_baseline', baseline)
            return {'saved':len(pending), 'directory':str(self.library)}

    def git(self, *args):
        self.writable()
        try:
            result = subprocess.run(['git', '-C', str(self.checkout), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, env={**os.environ, 'GIT_TERMINAL_PROMPT':'0'}, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(400, 'Git 操作不可用或超时：'+str(exc)) from exc
        if result.returncode:
            # 不回显可能包含凭据的 remote URL。
            raise HTTPException(409, 'Git 操作失败，请在源码终端检查远端、账号、冲突及 user.name/user.email；未自动合并或强制推送')
        return result.stdout.strip()

    def status(self):
        files = self.files()
        baseline = self.store.get_setting('case_repository_baseline', {})
        with self.store.session() as s:
            local = {row.id:digest(row.document) for row in s.scalars(select(Case)) if row.document.get('status') == 'draft'}
        result = {'directory':str(self.library), 'file_count':len(files), 'local_changes':sum(value != baseline.get(key) for key,value in local.items()), 'disk_changes':sum(digest(doc) != baseline.get(key) for key,(doc,_) in files.items())+len(baseline.keys()-files.keys()), 'git_available':False}
        try:
            relative = self.library.relative_to(self.checkout).as_posix() if self.checkout else ''
            result.update(git_available=True, git_status=self.git('status','--short','--',relative), diff=self.git('diff','HEAD','--',relative)[:50000] if self.git('rev-list','--all','--count') != '0' else '首次提交：尚无 HEAD；请审查用例文件及 Git 状态', has_remote=bool(self.git('remote')))
        except (HTTPException, ValueError):
            result.update(git_available=False)
        return result

    def action(self, action, message=''):
        with self.gate, self.manager.gate:
            self.writable()
            relative = self.library.relative_to(self.checkout).as_posix()
            if action == 'commit':
                if not message.strip() or len(message) > 500:
                    raise HTTPException(400, '请填写 1–500 字提交说明')
                self.save()
                self.git('add','--',relative)
                self.git('commit','--only','-m',message,'--',relative)
            elif action == 'pull':
                state = self.status()
                if state['local_changes'] or state['disk_changes'] or self.git('status','--porcelain'):
                    raise HTTPException(409, '源码工作区或本机用例有未提交变更；请先保存并提交，拒绝自动暂存/覆盖')
                self.git('pull','--ff-only')
                self.load()
            elif action == 'push':
                self.git('push')
            else:
                raise HTTPException(400, '未知 Git 操作')
            return self.status()

    def source(self, case_id):
        with self.store.session() as s:
            row = s.get(Case, case_id)
            if not row:
                raise HTTPException(404, '用例不存在')
            refs = row.document.get('migration', {}).get('source_files', [])
        sources = []
        for ref in refs:
            path = (self.library / ref).resolve()
            if not path.is_relative_to(self.library / 'sources') or path.suffix != '.txt':
                raise HTTPException(400, '迁移来源路径不安全')
            if path.is_file():
                sources.append({'file':ref, 'text':path.read_text(encoding='utf-8')})
        return sources
