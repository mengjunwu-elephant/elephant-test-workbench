"""无 Web/数据库依赖的步骤执行器，在选定 SDK 的 Python 环境运行。"""
from __future__ import annotations

import json
import math
import statistics
import threading
import time
from collections import deque
from uuid import uuid4

from .devices import Adapter


class Cancelled(Exception):
    pass


class Skipped(Exception):
    pass


class Control:
    def __init__(self):
        self.cancelled = threading.Event()
        self.responses: dict[str, str] = {}
        self.condition = threading.Condition()
        self.paused = threading.Event()

    def accept(self, command: dict):
        with self.condition:
            if command.get("action") == "stop":
                self.cancelled.set()
            elif command.get("action") == "pause":
                self.paused.set()
            elif command.get("action") == "resume":
                self.paused.clear()
            elif command.get("action") == "confirm":
                self.responses[command.get("id", "")] = command.get("answer", "skip")
            self.condition.notify_all()


def resolve(value, context):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            result = context
            for part in value["$ref"].split("."):
                result = result[int(part)] if isinstance(result, (list, tuple)) else result[part]
            return result
        return {k: resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, context) for v in value]
    return value


def compare(actual, expected, op, tolerance):
    if op == "eq":
        # bool 与 int 不可因 Python 的 True == 1 被误判。
        ok = type(actual) is type(expected) and actual == expected
    elif op == 'value_eq':
        # 迁移原 Python == 的语义；不改写参数或返回值类型。
        ok = actual == expected
    elif op == 'instance':
        kinds = {'int':int, 'float':float, 'str':str, 'list':list, 'tuple':tuple, 'dict':dict, 'bool':bool}
        if expected not in kinds:
            raise ValueError('不支持的类型名称')
        ok = isinstance(actual, kinds[expected])
    elif op == "approx":
        if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
            ok = len(actual) == len(expected) and all(type(a) in (int, float) and type(b) in (int, float) and math.isclose(a, b, abs_tol=tolerance, rel_tol=0) for a, b in zip(actual, expected))
        else:
            ok = type(actual) in (int, float) and type(expected) in (int, float) and math.isclose(actual, expected, abs_tol=tolerance, rel_tol=0)
    elif op == "range":
        ok = type(actual) in (int, float) and expected[0] <= actual <= expected[1]
    elif op == "type":
        ok = type(actual).__name__ == expected
    elif op == "truthy":
        ok = bool(actual)
    else:
        raise ValueError("不支持的断言")
    if not ok:
        raise AssertionError(f"断言失败：actual={actual!r}, expected={expected!r}, operator={op}, tolerance={tolerance}")


class Engine:
    def __init__(self, adapter: Adapter, emit, control: Control | None = None):
        self.adapter = adapter
        self.emit = emit
        self.control = control or Control()
        self.restoring = False
        self.last_telemetry = 0.0

    def event(self, kind, **fields):
        self.emit({"type": kind, "level": fields.pop("level", "INFO"), **fields})

    def checkpoint(self):
        if self.control.cancelled.is_set() and not self.restoring:
            raise Cancelled("收到停止请求")

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.checkpoint()
            time.sleep(min(0.05, max(0, end - time.monotonic())))

    def telemetry(self, force=False):
        if not force and time.monotonic() - self.last_telemetry < 0.25:
            return
        self.last_telemetry = time.monotonic()
        try:
            positions = self.adapter.position()
            moving = self.adapter.moving()
            self.event("telemetry", positions=positions, moving=moving, quality="ok" if positions is not None else "unsupported", source="hardware")
        except Exception as exc:
            self.event("telemetry", positions=None, moving=None, quality="error", message=str(exc), source="hardware", level="WARN")

    def wait_motion(self, timeout=15, poll_interval=0.1):
        end = time.monotonic() + timeout
        while True:
            self.checkpoint()
            moving = self.adapter.moving()
            if moving is None:
                raise RuntimeError("运动状态未知/不支持，不能判定完成")
            if not moving:
                completed_ns = time.perf_counter_ns()
                self.telemetry(force=True)
                return completed_ns
            if time.monotonic() >= end:
                raise TimeoutError("等待运动完成超时")
            self.telemetry()
            self.sleep(poll_interval)

    def call(self, api, args, kwargs):
        self.checkpoint()
        self.event("call", api=api, args=args, kwargs=kwargs, message=f"调用 {api}")
        begin = time.perf_counter_ns()
        try:
            result = self.adapter.invoke(api, args, kwargs)
        except Exception as exc:
            self.event("return", api=api, sdk_ms=(time.perf_counter_ns()-begin)/1e6, exception=type(exc).__name__, message=str(exc), level="ERROR")
            raise
        self.event("return", api=api, sdk_ms=(time.perf_counter_ns()-begin)/1e6, result=result, result_type=type(result).__name__, message=f"{api} 返回")
        return result

    def confirm(self, message, timeout=300):
        confirmation_id = uuid4().hex
        self.event("confirmation", id=confirmation_id, message=message)
        end = time.monotonic() + timeout
        with self.control.condition:
            while confirmation_id not in self.control.responses:
                self.checkpoint()
                if time.monotonic() >= end:
                    raise TimeoutError("人工确认超时，不自动放行")
                self.control.condition.wait(0.1)
            answer = self.control.responses.pop(confirmation_id)
        self.event("confirmation_answer", id=confirmation_id, answer=answer, message="收到人工确认操作")
        if answer != "continue":
            raise Skipped("人工跳过，执行恢复")
        self.event("state", state="running", message="人工确认后继续")

    def method(self, name, p, timeout):
        if name == "wait_motion":
            return self.wait_motion(p.get("timeout", timeout), p.get("poll_interval", 0.1))
        if name == "set_read_verify":
            self.call(p["set_api"], p.get("set_args", []), p.get("set_kwargs", {}))
            if p.get("wait_motion"):
                self.wait_motion(timeout)
            actual = self.call(p["get_api"], p.get("get_args", []), {})
            for key in p.get("selector", []):
                actual = actual[key]
            compare(actual, p["expected"], "approx" if p.get("tolerance", 0) else "eq", p.get("tolerance", 0))
            return actual
        if name == "restore_saved":
            self.call(p["api"], [p["value"], *p.get("extra_args", [])], {})
            self.wait_motion(timeout)
            actual = self.call(p["verify_api"], [], {})
            compare(actual, p["value"], "approx", p.get("tolerance", 0.1))
            return actual
        if name == "jog_until":
            seconds = float(p.get("seconds", 0.2))
            if not 0 < seconds <= timeout:
                raise ValueError("点动持续时间必须在当前超时内")
            self.call(p["api"], p.get("args", []), {})
            try:
                self.sleep(seconds)
            finally:
                old = self.restoring
                self.restoring = True
                try:
                    self.call(p["stop_api"], [], {})
                    self.wait_motion(timeout)
                finally:
                    self.restoring = old
            return None
        raise ValueError(f"未注册的公共方法：{name}")

    def step(self, step, context):
        self.event("step", step_id=step["id"], name=step["name"], phase=step.get("phase", "execute"), state="running", message=step["name"])
        kind = step["kind"]
        timeout = step.get("timeout", 15)
        value = None
        if kind in ("call", "read"):
            exception = step.get("expected_exception", "")
            try:
                value = self.call(step["api"], resolve(step.get("args", []), context), resolve(step.get("kwargs", {}), context))
            except Exception as exc:
                if not exception or exception not in [c.__name__ for c in type(exc).__mro__]:
                    raise
                value = {"expected_exception": type(exc).__name__, "message": str(exc)}
            else:
                if exception:
                    raise AssertionError(f"预期异常 {exception} 未抛出")
            for key in step.get("selector", []):
                value = value[key]
        elif kind == "assert":
            actual, expected = resolve(step.get("actual"), context), resolve(step.get("expected"), context)
            self.event("assertion", actual=actual, expected=expected, tolerance=step.get("tolerance", 0), message="比较实际与预期")
            compare(actual, expected, step.get("assertion", "eq"), step.get("tolerance", 0.1))
            value = actual
        elif kind == "wait":
            self.wait_motion(timeout)
        elif kind == "confirm":
            self.confirm(step.get("message", "请确认现场条件"), timeout)
        elif kind == "method":
            value = self.method(step["method"], resolve(step.get("parameters", {}), context), timeout)
        else:
            raise ValueError(f"未知步骤类型：{kind}")
        if step.get("save_as"):
            context[step["save_as"]] = value
        self.event("step", step_id=step["id"], name=step["name"], phase=step.get("phase", "execute"), state="passed", message=f"完成：{step['name']}")
        return value

    def case(self, case, dataset):
        context = {"params": {**case.get("parameters", {}), **dataset}}
        state, error, restore_error = "passed", "", ""
        self.event("case_start", case_id=case["id"], name=case["name"], parameters=context["params"], message=f"开始用例：{case['name']}")
        try:
            for step in case["steps"]:
                if step.get("phase") != "restore":
                    self.checkpoint()
                    self.step(step, context)
                    self.telemetry()
        except Skipped as exc:
            state, error = "skipped", str(exc)
        except Cancelled as exc:
            state, error = "stopped", str(exc)
        except Exception as exc:
            state, error = "failed", f"{type(exc).__name__}: {exc}"
            self.event("log", level="ERROR", message=error)
        finally:
            restores = [s for s in case["steps"] if s.get("phase") == "restore"]
            self.restoring = True
            if restores:
                self.event("state", state="restoring", message="执行恢复")
            try:
                for step in restores:
                    self.step(step, context)
            except Exception as exc:
                restore_error = f"{type(exc).__name__}: {exc}"
                self.event("log", level="ERROR", message="恢复失败：" + restore_error)
            finally:
                self.restoring = False
        # 实机异常没有配置恢复或不能取得静止状态时，阻断后续任务。
        unknown = bool(restore_error)
        try:
            unknown = unknown or self.adapter.moving() is not False or (state != "passed" and not restores)
        except Exception:
            unknown = True
        result = {"case_id": case["id"], "name": case["name"], "parameters": context["params"], "state": "failed" if restore_error else state, "error": error, "restore_error": restore_error, "restored": bool(restores) and not restore_error, "blocked": unknown}
        self.event("case_result", **result, message="用例完成，恢复与原始结果分别记录")
        return result

    def run_cases(self, request, cases):
        results = deque(maxlen=500)
        counts = {"passed": 0, "failed": 0, "skipped": 0, "stopped": 0}
        begin, consecutive, cycles = time.monotonic(), 0, 0
        limit = request.get("repeat", 1) if request["kind"] == "aging" else 1
        while cycles < limit:
            self.checkpoint()
            if cycles and request["kind"] == "aging" and time.monotonic()-begin >= request["duration_seconds"]:
                break
            if self.control.paused.is_set():
                self.event("state", state="paused", message="已在循环边界暂停")
                while self.control.paused.is_set():
                    self.sleep(0.1)
                self.event("state", state="running", message="恢复运行")
            for case in cases:
                for dataset in case.get("datasets", [{}]):
                    result = self.case(case, dataset)
                    results.append(result)
                    counts[result["state"]] += 1
                    consecutive = consecutive + 1 if result["state"] == "failed" else 0
                    if result["blocked"]:
                        return {"results": list(results), "counts": counts, "blocked": True, "state": "blocked", "reason": "恢复失败或设备状态不明，停止该设备后续任务"}
                    self.checkpoint()
                    self.event("state", state="running", message="恢复完成，继续下一条")
                    if request["kind"] == "aging" and consecutive >= request.get("max_consecutive_failures", 3):
                        return {"results": list(results), "counts": counts, "blocked": False, "state": "failed", "reason": "达到连续失败阈值"}
            cycles += 1
            self.event("cycle", cycle=cycles, elapsed_seconds=time.monotonic()-begin, message=f"完成第 {cycles} 轮")
            if request["kind"] == "aging":
                self.sleep(request.get("interval_ms", 100)/1000)
        return {"results": list(results), "counts": counts, "cycles": cycles, "blocked": False, "state": "failed" if counts["failed"] else "completed"}

    def latency(self, request):
        samples, blocked, restore_error = deque(maxlen=1000), False, ""
        valid_sdk, valid_motion, sdk_failures, motion_failures = [], [], 0, 0
        measure_motion = request.get("measure_motion", False)
        motion_apis = {"set_angle", "set_angles", "send_angle", "send_angles", "set_coords", "send_coords", "set_coord", "send_coord"}
        is_motion = request["api"] in motion_apis
        if measure_motion and not is_motion:
            raise ValueError("运动完成耗时需要选择运动接口，不能为只读接口伪造运动时间")
        initial = self.adapter.position() if is_motion else None
        try:
            for i in range(request.get("repeat", 1)):
                self.checkpoint()
                start = time.perf_counter_ns()
                record = {"index": i+1, "sdk_ms": None, "motion_ms": None, "sdk_status": "pending", "motion_status": "not_requested"}
                try:
                    result = self.adapter.invoke(request["api"], request.get("args", []), request.get("kwargs", {}))
                    record.update(sdk_ms=(time.perf_counter_ns()-start)/1e6, sdk_status="ok", result=result)
                    if is_motion:
                        try:
                            completed_ns = self.wait_motion(request.get("timeout", 15))
                            if measure_motion:
                                record.update(motion_ms=(completed_ns-start)/1e6, motion_status="ok")
                        except Exception as exc:
                            record.update(motion_status="failed", motion_error=str(exc))
                            raise
                except Cancelled:
                    raise
                except Exception as exc:
                    if record["sdk_status"] == "pending":
                        record.update(sdk_ms=(time.perf_counter_ns()-start)/1e6, sdk_status="failed", error=f"{type(exc).__name__}: {exc}")
                    elif not record.get("motion_error"):
                        record["error"] = str(exc)
                samples.append(record)
                if record["sdk_status"] == "ok":
                    valid_sdk.append(record["sdk_ms"])
                else:
                    sdk_failures += 1
                if record["motion_status"] == "ok":
                    valid_motion.append(record["motion_ms"])
                elif record["motion_status"] == "failed":
                    motion_failures += 1
                self.event("sample", **record, message=f"第 {i+1} 次采样；SDK 与运动分别统计")
                if record["motion_status"] == "failed":
                    break
                self.sleep(request.get("interval_ms", 100)/1000)
        finally:
            if is_motion:
                self.restoring = True
                try:
                    if initial is None:
                        raise RuntimeError("没有可用于恢复的初始位置")
                    self.call(self.adapter.move_all_api, [list(initial), 20], {})
                    self.wait_motion(request.get("timeout", 15))
                    compare(self.adapter.position(), initial, "approx", 0.1)
                except Exception as exc:
                    blocked, restore_error = True, str(exc)
                finally:
                    self.restoring = False

        def stats(valid):
            return {"count": len(valid), "mean_ms": statistics.mean(valid) if valid else None, "min_ms": min(valid) if valid else None, "max_ms": max(valid) if valid else None, "median_ms": statistics.median(valid) if valid else None}

        failed = bool(sdk_failures or motion_failures)
        return {"samples": list(samples), "sample_count": len(valid_sdk)+sdk_failures, "sdk_failures": sdk_failures, "motion_failures": motion_failures, "sdk": stats(valid_sdk), "motion": stats(valid_motion), "blocked": blocked, "restore_error": restore_error, "state": "blocked" if blocked else "failed" if failed else "completed"}

