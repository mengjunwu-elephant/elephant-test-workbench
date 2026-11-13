"""执行端设备适配。导入此模块不会导入 SDK 或建立连接。"""
from __future__ import annotations

import importlib
import inspect
import math
import socket

from .catalog import MODELS


class TransportConnectionError(ConnectionError):
    pass


def bounded_socket_client(cls, timeout):
    def connect_socket(client):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((client.SERVER_IP, client.SERVER_PORT))
            sock.settimeout(None)  # SDK's existing reader retains its normal blocking behavior.
            return sock
        except OSError as exc:
            sock.close()
            raise TransportConnectionError(f'TCP 连接失败：{client.SERVER_IP}:{client.SERVER_PORT}（超时 {timeout:g} 秒）：{exc}') from exc
    # SDK validation branches on the class name; preserve it.
    return type(cls.__name__, (cls,), {'connect_socket': connect_socket})


def public_apis(cls):
    result = []
    for name, obj in inspect.getmembers(cls, callable):
        if name.startswith("_"):
            continue
        try:
            sig = inspect.signature(obj)
            fields = []
            for p in sig.parameters.values():
                if p.name == "self":
                    continue
                default = None if p.default is inspect.Parameter.empty else p.default
                fields.append({"name": p.name, "kind": p.kind.name, "required": p.default is inspect.Parameter.empty, "default": default if isinstance(default, (str, int, float, bool, list, dict, type(None))) else repr(default)})
            result.append({"name": name, "signature": str(sig), "parameters": fields, "description": (inspect.getdoc(obj) or "")[:1500]})
        except (ValueError, TypeError):
            continue
    return result


def describe(model: str):
    import importlib.metadata
    profile = next(x for x in MODELS if x["id"] == model)
    cls = getattr(importlib.import_module("pymycobot"), profile["class"])
    return {"source": "installed-sdk", "sdk_version": importlib.metadata.version("pymycobot"), "class": profile["class"], "apis": public_apis(cls)}


class Adapter:
    def __init__(self, config: dict):
        self.config = config
        if config.get("mode", "hardware") != "hardware":
            raise ValueError("只支持真实设备连接")
        self.clients = []
        if not config.get("address", "").strip():
            raise ValueError("实机连接地址不能为空")
        profile = next(x for x in MODELS if x["id"] == config["model"])
        cls = getattr(importlib.import_module("pymycobot"), profile["class"])
        address = config["address"].strip()
        if config["model"] == "pro450":
            client = bounded_socket_client(cls, config.get('connect_timeout', 8))(ip=address, netport=config.get('netport', 4500), debug=False)
        elif config["model"] == "ultraarm_p1":
            client = cls(address, config.get("baudrate", 115200), debug=False)
        elif config["model"] == "mycobot280":
            client = cls(address, baudrate=config.get("baudrate", 115200), debug=False)
        elif config["model"] == "mercury_e1":
            client = cls(port=address, debug=False)
        else:
            second = config.get("second_address", "").strip()
            if not second:
                raise ValueError("双臂需要明确提供左右串口")
            left = cls(address, debug=False)
            self.clients.append(left)
            try:
                right = cls(second, debug=False)
            except BaseException:
                self.close()
                raise
            self.clients.append(right)
            client = right if config.get("arm") == "right" else left
        self.device = client
        if not self.clients:
            self.clients.append(client)
        self.allowed = {x["name"] for x in public_apis(type(self.device))}
        self.position_api = "get_angles_info" if config["model"] == "ultraarm_p1" else "get_angles"
        self.motion_api = "get_run_status" if config["model"] == "ultraarm_p1" else "is_moving"
        self.move_all_api = "set_angles" if config["model"] == "ultraarm_p1" else "send_angles"

    def invoke(self, api: str, args: list, kwargs: dict):
        if api not in self.allowed or api.startswith("_"):
            raise ValueError(f"当前 SDK/机型未提供公开接口：{api}")
        return getattr(self.device, api)(*args, **kwargs)

    def position(self):
        if self.position_api not in self.allowed:
            return None
        value = self.invoke(self.position_api, [], {})
        return value if isinstance(value, (list, tuple)) and value and all(type(v) in (int, float) and math.isfinite(v) for v in value) else None

    def moving(self):
        if self.motion_api not in self.allowed:
            return None
        value = self.invoke(self.motion_api, [], {})
        if value in (0, False):
            return False
        if value in (1, True):
            return True
        return None

    def versions(self):
        """只调用已知的只读版本接口；屏幕与底座版本不混为一谈。"""
        from datetime import datetime, timezone
        import importlib.metadata
        fields = {
            'controller': ('主控版本', 'get_system_version'),
            'tool': ('末端版本', 'get_atom_version'),
            'screen': ('屏幕版本', 'get_system_screen_version'),
            'controller_revision': ('主控修订版本', 'get_modified_version' if self.config['model'] in ('pro450', 'mercury', 'mercury_e1') else 'get_modify_version'),
            'tool_revision': ('末端修订版本', 'get_tool_modify_version'),
            'screen_revision': ('屏幕修订版本', 'get_modify_screen_version'),
        }
        result = {}
        for key, (label, api) in fields.items():
            item = {'label': label, 'api': api, 'status': 'unsupported', 'value': None}
            if api in self.allowed:
                try:
                    value = self.invoke(api, [], {})
                    if value is None or value == '' or value == -1 or isinstance(value, bool):
                        raise ValueError(f'版本返回无效：{value!r}')
                    item.update(status='ok', value=value)
                except Exception as exc:
                    item.update(status='error', error=str(exc))
            result[key] = item
        try:
            sdk = importlib.metadata.version('pymycobot')
        except importlib.metadata.PackageNotFoundError:
            sdk = 'unknown'
        return {'model': self.config['model'], 'arm': self.config.get('arm', 'left'), 'sdk_version': sdk, 'read_at': datetime.now(timezone.utc).isoformat(), 'fields': result}

    def close(self):
        for client in self.clients:
            try:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
                else:
                    port = getattr(client, "_serial_port", None)
                    if port is not None:
                        port.close()
            except Exception:
                pass

