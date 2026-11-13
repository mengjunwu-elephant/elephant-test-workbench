from __future__ import annotations

MODELS = [
    {"id": "ultraarm_p1", "name": "UltraArm P1", "connection": "serial", "class": "UltraArmP1"},
    {"id": "pro450", "name": "MyCobot Pro 450", "connection": "ip", "class": "Pro450Client"},
    {"id": "mercury_e1", "name": "Mercury E1", "connection": "serial", "class": "MercuryE1"},
    {"id": "mercury", "name": "Mercury X1 双臂", "connection": "dual_serial", "class": "Mercury"},
    {"id": "mycobot280", "name": "MyCobot 280", "connection": "serial", "class": "MyCobot280"},
]

METHODS = [
    {"id": "wait_motion", "name": "等待运动完成", "version": "1.0", "parameters": {"timeout": 15, "poll_interval": 0.1}, "description": "执行端有界等待；运动状态接口按机型适配。"},
    {"id": "set_read_verify", "name": "设置后读回验证", "version": "1.0", "parameters": {"set_api": "", "set_args": [], "get_api": "", "get_args": [], "expected": None, "tolerance": 0}, "description": "设置、读回与比较；具体 SDK 方法由当前环境提供。"},
    {"id": "restore_saved", "name": "恢复已保存状态", "version": "1.0", "parameters": {"api": "set_angles", "value": {"$ref": "initial_angles"}, "extra_args": [20], "verify_api": "get_angles_info", "tolerance": 0.1}, "description": "恢复前序保存状态并验证，不推断所有机型都有相同接口。"},
    {"id": "jog_until", "name": "有界点动与停止", "version": "1.0", "parameters": {"api": "set_jog_angle", "args": [1, 1, 20], "seconds": 0.2, "stop_api": "stop"}, "description": "有界点动后发送适配的停止指令；实际停止结果仍需等待验证。"},
]
