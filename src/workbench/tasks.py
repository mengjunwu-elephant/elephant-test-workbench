from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import HTTPException
from packaging.specifiers import SpecifierSet
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import __version__
from .catalog import MODELS, METHODS
from .environments import creation_options
from .models import RunRequest, uid
from .storage import Store, Task, DeviceLock, Environment, Case, now
from .worker import PREFIX
from .processes import terminate_worker

TERMINAL = {"completed", "failed", "blocked", "stopped", "interrupted"}


def resource_key(connection):
    addresses = [connection.get("address", ""), connection.get("second_address", "")]
    if connection.get('model')=='pro450':
        address=connection.get('address','').strip()
        port=connection.get('netport',4500)
        addresses=[address if port==4500 else f'{address}:{port}']
    normalized = sorted({x.strip().lower().removeprefix("\\\\.\\") for x in addresses if x.strip()})
    # 不含机型：同一个连接目标不能通过换机型绕过占用。
    return "hardware:" + "|".join(normalized)


def resource_keys(connection):
    key = resource_key(connection)
    return ["hardware:"+address for address in key.removeprefix("hardware:").split("|")]


class TaskManager:
    def __init__(self, store: Store):
        self.store = store
        self.processes: dict[str, subprocess.Popen] = {}
        self.gate = threading.RLock()
        self.session_jobs = {}
        self.job_sessions = {}
        self.forced_failures = {}
        self.startup_stages = {}
        self._recover_interrupted()

    def _recover_interrupted(self):
        with self.store.session() as s:
            for task in s.scalars(select(Task).where(Task.state.not_in(TERMINAL))):
                task.state, task.ended_at = "interrupted", now()
                task.summary = {"reason": "应用重启；未完成任务状态待核实，不自动恢复运动"}
                for lock in s.scalars(select(DeviceLock).where(DeviceLock.task_id == task.id)):
                    if task.resource.startswith("hardware:"):
                        lock.quarantined, lock.reason = True, "执行中断，需现场确认设备状态"
                    else:
                        s.delete(lock)
            for env in s.scalars(select(Environment).where(Environment.state == "preparing")):
                env.state, env.details = "failed", {**env.details, "error": "环境准备进程中断，请重新导入"}

    def has_running(self):
        with self.store.session() as s:
            return bool(s.scalar(select(Task.id).where(Task.state.not_in(TERMINAL)).limit(1)))

    def resolve_environment(self, request):
        with self.store.session() as s:
            env = s.get(Environment, request.connection.environment_id) if request.connection.environment_id else s.scalars(select(Environment).where(Environment.active.is_(True))).first()
            if not env or env.state != "ready" or not Path(env.python).is_file():
                raise HTTPException(400, "请先导入并启用离线 SDK 环境")
            return env.python, {"id": env.id, **env.details}

    def start(self, request: RunRequest):
        with self.gate:
            if request.connection.model not in {x["id"] for x in MODELS}:
                raise HTTPException(400, "未知机型")
            if request.kind == "latency" and request.measure_motion and request.api not in {"set_angle", "set_angles", "send_angle", "send_angles", "set_coord", "set_coords", "send_coord", "send_coords"}:
                raise HTTPException(400, "运动完成耗时需要选择运动接口；只读接口不能生成运动时间")
            if not request.hardware_confirmed:
                raise HTTPException(400, "实机任务需要明确确认连接配置与现场条件")
            if not request.connection.address.strip():
                raise HTTPException(400, "请先配置实机串口或控制器 IP")
            if request.connection.model == "mercury" and not request.connection.second_address.strip():
                raise HTTPException(400, "双臂需要配置左右串口")
            python, environment = self.resolve_environment(request)
            cases = [c.model_dump() for c in request.inline_cases]
            with self.store.session() as s:
                for inline in cases:
                    if inline.get("status") == "shared":
                        published = s.get(Case, inline["id"])
                        if not published or published.document != inline:
                            raise HTTPException(409, "共享正式版不可临时改写后冒充原版本执行，请先复制为草稿")
                stored = s.scalars(select(Case)).all()
                for row in stored:
                    doc = row.document
                    if (request.all_cases or row.id in request.case_ids) and doc["model"] == request.connection.model:
                        if request.all_cases and not doc.get('enabled', True):
                            continue
                        if doc.get("category") == "manual" and not request.include_manual:
                            continue
                        cases.append(doc)
            if request.kind in ("cases", "aging") and not cases:
                raise HTTPException(400, "没有选中适用于当前机型的用例")
            for case in cases:
                if not case.get('enabled', True):
                    raise HTTPException(409, '用例尚待完成迁移或人工审核，不能执行：'+case['name'])
                if case["model"] != request.connection.model:
                    raise HTTPException(400, "用例机型与执行机型不同")
                if case.get("sdk_requirement"):
                    if environment.get("sdk_version", "") not in SpecifierSet(case["sdk_requirement"]):
                        raise HTTPException(409, "用例与当前 SDK 版本条件不兼容")
                available = {m["id"]: m["version"] for m in METHODS}
                if any(available.get(k) != v for k, v in case.get("method_versions", {}).items()):
                    raise HTTPException(409, "公共方法版本不匹配，请更新平台或使用匹配用例")
            task_id = uid()
            resource = resource_key(request.connection.model_dump())
            snapshot = {"platform_version": __version__, "system": platform.system(), "environment": environment, "cases": cases, "methods": METHODS, "model": request.connection.model, "source": "hardware"}
            with self.store.session() as s:
                held=[s.get(DeviceLock,key) for key in resource_keys(request.connection.model_dump())]
                owners={lock.task_id for lock in held if lock}
                owner=s.get(Task,next(iter(owners))) if len(owners)==1 else None
                if any(held):
                    if any(lock.quarantined for lock in held if lock):
                        if request.kind != 'connection':
                            raise HTTPException(409,'连接已经失效，请先重新连接并完成状态校验')
                        for lock in held:
                            if not lock:
                                continue
                            previous = s.get(Task, lock.task_id)
                            process = self.processes.get(lock.task_id)
                            if (process and process.poll() is None) or (previous and previous.state not in TERMINAL):
                                raise HTTPException(409,'原连接仍在退出，请等待进程结束后重连')
                            self.store.append_event(lock.task_id, {'type':'reconnect_requested','message':'用户请求重新连接；保留原始故障记录，重新校验设备状态','previous_reason':lock.reason})
                            s.delete(lock)
                        s.flush()
                        held = []
                        owner = None
                if any(held):
                    allowed_states = ('connected', 'degraded') if request.kind == 'debug' else ('connected',)
                    if not owner or owner.kind!='connection' or owner.state not in allowed_states or request.kind=='connection':
                        raise HTTPException(409,f'连接正在建立或执行任务，请先查看测试执行页（任务 {owner.id[:8] if owner else "未知"}）')
                    if owner.id in self.session_jobs:
                        raise HTTPException(409,'同一连接已有接口任务正在执行，请等待完成')
                    old=owner.request['connection']
                    if any(old.get(k)!=request.connection.model_dump().get(k) for k in ('model','address','second_address','arm','netport')) or owner.snapshot['environment'].get('id')!=environment.get('id'):
                        raise HTTPException(409,'当前连接配置或 SDK 已变化，请先断开再重新连接')
                    process=self.processes.get(owner.id)
                    if not process or process.poll() is not None:
                        raise HTTPException(409,'连接进程已经退出，请先查看连接任务结果')
                    snapshot['device_info'] = owner.summary.get('device_info', {})
                    s.add(Task(id=task_id,kind=request.kind,state='queued',resource=resource,request=request.model_dump(),snapshot=snapshot,summary={},telemetry={}))
                    owner_id=owner.id
                else:
                    owner_id=None
            if owner_id:
                run_dir=self.store.run_dir(task_id)
                payload={'request':request.model_dump(),'cases':cases}
                (run_dir/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
                (run_dir/'payload.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
                self.session_jobs[owner_id]=task_id
                self.job_sessions[task_id]=owner_id
                self.processes[task_id]=process
                try:
                    self._send(process,{'action':'run','task_id':task_id,'payload':payload})
                except Exception:
                    self._finish(task_id,{'state':'blocked','blocked':True,'error':'向连接进程提交任务失败'})
                    self.session_jobs.pop(owner_id,None)
                    self.job_sessions.pop(task_id,None)
                    self.processes.pop(task_id,None)
                    raise
                return task_id
            try:
                with self.store.session() as s:
                    for key in resource_keys(request.connection.model_dump()):
                        s.add(DeviceLock(resource=key, task_id=task_id, quarantined=False))
                    s.add(Task(id=task_id, kind=request.kind, state="queued", resource=resource, request=request.model_dump(), snapshot=snapshot, summary={}, telemetry={}))
            except IntegrityError as exc:
                raise HTTPException(409, "当前连接已被任务占用，或处于状态待核实隔离状态") from exc
            run_dir = self.store.run_dir(task_id)
            payload = {"request": request.model_dump(), "cases": cases}
            (run_dir / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            (run_dir / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            command = [python, "-u", "-m", "workbench.worker", "--payload", str(run_dir / "payload.json")]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
            try:
                process = subprocess.Popen(command, start_new_session=(os.name != "nt"), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **creation_options())
            except Exception:
                with self.store.session() as s:
                    for lock in s.scalars(select(DeviceLock).where(DeviceLock.task_id == task_id)):
                        s.delete(lock)
                    task = s.get(Task, task_id)
                    task.state, task.ended_at = "failed", now()
                raise
            self.processes[task_id] = process
            ready=threading.Event()
            threading.Thread(target=self._watch,args=(task_id,process,ready),daemon=True).start()
            threading.Thread(target=self._startup_watchdog,args=(task_id,process,ready,request.connection.connect_timeout+3),daemon=True).start()
            return task_id

    def _send(self, process, command):
        process.stdin.write(json.dumps(command)+'\n')
        process.stdin.flush()

    def _finish(self, task_id, summary):
        with self.store.session() as s:
            row=s.get(Task,task_id)
            if not row:
                return
            row.summary=summary
            row.state,row.ended_at=summary['state'],now()
            for lock in s.scalars(select(DeviceLock).where(DeviceLock.task_id==task_id)):
                if summary.get('blocked'):
                    lock.quarantined,lock.reason=True,summary.get('reason',summary.get('error','状态待核实'))
                else:
                    s.delete(lock)
        self.store.append_event(task_id,{'type':'finished','state':summary['state'],'message':'任务已结束，结果保存在本机'})

    def _startup_watchdog(self, task_id, process, ready, timeout):
        if not ready.wait(timeout) and process.poll() is None:
            stage=self.startup_stages.get(task_id,{})
            loading=stage.get('stage')=='sdk_import'
            reason=(f'SDK 加载超过 {timeout:g} 秒，尚未发起设备连接' if loading else f'连接或状态校验超过 {timeout:g} 秒，阶段：{stage.get("message","执行器启动")}；设备状态待核实')
            self.forced_failures[task_id]={'state':'failed' if loading else 'blocked','blocked':not loading,'error':reason,'connection_stage':stage.get('stage','worker_start')}
            self.store.append_event(task_id,{'type':'log','level':'ERROR','message':reason})
            terminate_worker(process)

    def _exit_watchdog(self, task_id, process):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.forced_failures[task_id]={'state':'blocked','blocked':True,'error':'执行进程清理超时，设备状态待核实'}
            terminate_worker(process)

    def _watch(self, task_id, process, ready):
        summary,last_telemetry_write=None,0.0
        try:
            for line in process.stdout:
                if PREFIX not in line:
                    if line.strip():
                        self.store.append_event(self.session_jobs.get(task_id,task_id),{'type':'log','level':'INFO','message':line.rstrip()[:12000]})
                    continue
                try:
                    event=json.loads(line.split(PREFIX,1)[1])
                except ValueError:
                    continue
                target=event.get('task_id',task_id)
                if target!=task_id and self.job_sessions.get(target)!=task_id:
                    continue
                self.store.append_event(target,event)
                kind=event.get('type')
                if kind=='connection_stage':
                    self.startup_stages[task_id]=event
                if kind=='job_complete':
                    with self.gate:
                        self._finish(target,event['summary'])
                        self.processes.pop(target,None)
                        self.job_sessions.pop(target,None)
                        self.session_jobs.pop(task_id,None)
                    continue
                if kind=='complete':
                    summary=event['summary']
                    ready.set()
                    threading.Thread(target=self._exit_watchdog,args=(task_id,process),daemon=True).start()
                if kind=='state' and event.get('state') in ('running','connected') and target==task_id:
                    ready.set()
                with self.store.session() as s:
                    row=s.get(Task,target)
                    if kind=='state' and row.state not in ('stopping','disconnecting'):
                        row.state=event['state']
                    elif kind=='device_info':
                        row.summary={**row.summary,'device_info':event['info']}
                    elif kind=='confirmation':
                        row.state='waiting_confirmation'
                        row.summary={**row.summary,'confirmation':event}
                    elif kind=='confirmation_answer':
                        row.summary={k:v for k,v in row.summary.items() if k!='confirmation'}
                    elif kind=='telemetry' and time.monotonic()-last_telemetry_write>.2:
                        row.telemetry={**event,'time':now()}
                        if target!=task_id:
                            s.get(Task,task_id).telemetry=row.telemetry
                        last_telemetry_write=time.monotonic()
            process.wait()
        except Exception as exc:
            summary={'state':'blocked','blocked':True,'error':'任务监视异常：'+str(exc)}
            if process.poll() is None:
                terminate_worker(process)
        finally:
            ready.set()
            with self.gate:
                summary=self.forced_failures.pop(task_id,None) or summary or {'state':'blocked','blocked':True,'error':f'执行进程意外结束（{process.returncode}），状态待核实'}
                job=self.session_jobs.pop(task_id,None)
                if job:
                    self._finish(job,{'state':'blocked','blocked':True,'error':summary.get('error','连接进程已退出')})
                    self.job_sessions.pop(job,None)
                    self.processes.pop(job,None)
                self._finish(task_id,summary)
                self.processes.pop(task_id,None)
                self.startup_stages.pop(task_id,None)

    def control(self, task_id, action, confirmation_id="", answer=""):
        with self.gate:
            process = self.processes.get(task_id)
            if not process or process.poll() is not None:
                raise HTTPException(409, "任务已结束或不由当前服务持有")
            owner_id=self.job_sessions.get(task_id)
            with self.store.session() as s:
                is_connection=s.get(Task,task_id).kind=='connection'
            if is_connection:
                if task_id in self.session_jobs:
                    raise HTTPException(409,'连接上有任务运行，请先停止该任务并完成恢复')
                if action not in ('disconnect','stop'):
                    raise HTTPException(400,'连接会话仅支持断开')
                self._send(process,{'action':'disconnect'})
                with self.store.session() as s:
                    s.get(Task,task_id).state='disconnecting'
                threading.Thread(target=self._stop_watchdog,args=(task_id,process),daemon=True).start()
                return
            if action not in ("stop", "pause", "resume", "confirm"):
                raise HTTPException(400, "未知操作")
            if action == "confirm":
                with self.store.session() as s:
                    prompt = s.get(Task, task_id).summary.get("confirmation", {})
                    if prompt.get("id") != confirmation_id or answer not in ("continue", "skip"):
                        raise HTTPException(409, "确认请求已失效或答案无效")
            self._send(process,{'action':action,'id':confirmation_id,'answer':answer,**({'task_id':task_id} if owner_id else {})})
            if action == "stop":
                with self.store.session() as s:
                    s.get(Task, task_id).state = "stopping"
                threading.Thread(target=self._stop_watchdog, args=(task_id, process), daemon=True).start()
        self.store.append_event(task_id, {"type": "control", "action": action, "message": {"stop": "停止请求已提交，等待恢复与设备状态", "pause": "请求在老化循环边界暂停", "resume": "请求继续", "confirm": "人工确认已提交"}[action]})

    def _stop_watchdog(self, task_id, process):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            with self.store.session() as s:
                row=s.get(Task,task_id)
                if not row or row.state in TERMINAL:
                    return
            if process.poll() is not None:
                return
            time.sleep(.1)
        owner=self.job_sessions.get(task_id,task_id)
        reason='停止或断开超时，结束执行进程树；不代表机械臂停止'
        self.forced_failures[owner]={'state':'blocked','blocked':True,'error':reason}
        self.store.append_event(task_id,{'type':'log','level':'ERROR','message':reason})
        terminate_worker(process)

    def shutdown(self):
        with self.gate:
            running = list(self.processes.items())
        for task_id, _ in running:
            try:
                self.control(task_id, "stop")
            except Exception:
                pass
        for _, process in running:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                terminate_worker(process)
