"""跨平台入口；只启动本机 Web 服务，不连接机械臂。"""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parent
python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not python.is_file():
    raise SystemExit("未创建项目环境。先运行 python scripts/bootstrap.py；详见 README.md。")
command = [str(python), "-m", "workbench", "run", "--open", *sys.argv[1:]]
raise SystemExit(subprocess.call(command, cwd=root))
