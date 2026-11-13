"""Internal test doubles, never imported by the application or included in its wheel."""
import time
from typing import Any
class FakeRobot:
    """明确的工作流模拟器；限值不代表任何产品的真实规格。"""
    def __init__(self, *args, **kwargs):
        self.angles = [0.0, 10.0, 110.0, 0.0]
        self.start_angles = self.angles[:]
        self.target = self.angles[:]
        self.started = 0.0
        self.until = 0.0
        self.parameters: dict[str, Any] = {}

    def _advance(self):
        now = time.monotonic()
        if self.until <= self.started:
            return
        fraction = min(1.0, max(0.0, (now - self.started) / (self.until - self.started)))
        self.angles = [a + (b - a) * fraction for a, b in zip(self.start_angles, self.target)]

    def get_angles_info(self):
        self._advance()
        return self.angles[:]

    def get_angles(self):
        return self.get_angles_info()

    def set_angle(self, joint, angle, speed):
        if type(joint) is not int or not 1 <= joint <= 4:
            raise ValueError("模拟器关节编号应为 1–4；不是产品参数规格")
        if type(angle) not in (float, int) or not -180 <= angle <= 180:
            raise ValueError("超出模拟器演示范围；真实 SDK 规格由产品定义")
        if type(speed) not in (float, int) or not 1 <= speed <= 100:
            raise ValueError("超出模拟器演示速度范围")
        self._advance()
        self.start_angles = self.angles[:]
        self.target = self.angles[:]
        self.target[joint - 1] = float(angle)
        self.started = time.monotonic()
        self.until = self.started + 0.16
        return "SIMULATED_ACK"

    def send_angle(self, joint, angle, speed):
        return self.set_angle(joint, angle, speed)

    def set_angles(self, angles, speed):
        if not isinstance(angles, list) or len(angles) != 4:
            raise ValueError("模拟器需要四个关节角度")
        if any(type(v) not in (float, int) or not -180 <= v <= 180 for v in angles):
            raise ValueError("超出模拟器演示范围")
        self._advance()
        self.start_angles = self.angles[:]
        self.target = [float(v) for v in angles]
        self.started = time.monotonic()
        self.until = self.started + 0.16
        return "SIMULATED_ACK"

    def send_angles(self, angles, speed):
        return self.set_angles(angles, speed)

    def set_jog_angle(self, joint, position, speed):
        if position not in (0, 1):
            raise ValueError("模拟器方向为 0/1")
        self._advance()
        return self.set_angle(joint, self.angles[joint - 1] + (5 if position else -5), speed)

    def get_run_status(self):
        self._advance()
        return int(time.monotonic() < self.until)

    def is_moving(self):
        return self.get_run_status()

    def stop(self):
        self._advance()
        self.target = self.angles[:]
        self.until = self.started = time.monotonic()
        return "SIMULATED_STOP"

    def close(self):
        return None


def seed_cases():
    common = dict(model="ultraarm_p1", revision=1, status="draft", method_versions={"wait_motion": "1.0"}, datasets=[{}])
    return [
        {**common, "id": "demo-read", "name": "模拟 · 读取关节位置", "description": "仅在显式模拟模式验证工作流；不是实机证据。", "category": "normal", "parameters": {}, "steps": [
            {"id": "read", "name": "读取位置", "kind": "read", "api": "get_angles_info", "save_as": "angles"},
            {"id": "assert", "name": "验证数组类型", "phase": "verify", "kind": "assert", "actual": {"$ref": "angles"}, "expected": "list", "assertion": "type"}
        ]},
        {**common, "id": "demo-angle", "name": "模拟 · 单关节设置与恢复", "description": "可参数化运行；关节边界由每条用例填写。", "category": "normal", "parameters": {"joint": 1, "angle": 30, "speed": 20}, "steps": [
            {"id": "save", "name": "保存初始角度", "phase": "setup", "kind": "read", "api": "get_angles_info", "save_as": "initial_angles"},
            {"id": "move", "name": "设置目标角度", "kind": "call", "api": "set_angle", "args": [{"$ref": "params.joint"}, {"$ref": "params.angle"}, {"$ref": "params.speed"}]},
            {"id": "wait", "name": "等待运动完成", "kind": "wait", "timeout": 10},
            {"id": "read", "name": "读取实际角度", "kind": "read", "api": "get_angles_info", "save_as": "actual"},
            {"id": "verify", "name": "验证位置与容差", "phase": "verify", "kind": "assert", "actual": {"$ref": "actual.0"}, "expected": {"$ref": "params.angle"}, "assertion": "approx", "tolerance": 0.1},
            {"id": "restore", "name": "恢复初始位置并验证", "phase": "restore", "kind": "method", "method": "restore_saved", "parameters": {"api": "set_angles", "value": {"$ref": "initial_angles"}, "extra_args": [20], "verify_api": "get_angles_info", "tolerance": 0.1}}
        ]},
        {**common, "id": "demo-invalid", "name": "模拟 · 超限参数异常", "category": "exception", "description": "模拟器仅用于流程验证；实机异常类型需按 SDK 配置。", "parameters": {}, "steps": [
            {"id": "invalid", "name": "超限输入应被拒绝", "kind": "call", "api": "set_angle", "args": [1, 999, 20], "expected_exception": "ValueError"}
        ]},
    ]


def device():
    import sys
    from types import SimpleNamespace
    from unittest.mock import patch
    from workbench.devices import Adapter
    with patch.dict(sys.modules, {"pymycobot": SimpleNamespace(UltraArmP1=FakeRobot)}):
        return Adapter({"mode": "hardware", "model": "ultraarm_p1", "address": "TEST-ONLY"})
