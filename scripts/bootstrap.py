"""创建本应用环境。SDK 更新始终走界面离线导入，与此环境分开。"""
import argparse
import os
from pathlib import Path
import subprocess
import venv

parser = argparse.ArgumentParser()
parser.add_argument("--wheelhouse", type=Path, help="可选：本应用所需全部 wheel 的离线目录")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
environment = root / ".venv"
venv.EnvBuilder(with_pip=True).create(environment)
python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
command = [str(python), "-m", "pip", "install"]
command += ["--find-links", str(root / "src" / "workbench" / "bundled_wheels")]
if args.wheelhouse:
    subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(args.wheelhouse.resolve()), "setuptools>=75", "wheel"], check=True, cwd=root)
    command += ["--no-index", "--find-links", str(args.wheelhouse.resolve()), "--no-build-isolation"]
command += ["-e", str(root) + "[test]"]
subprocess.run(command, check=True, cwd=root)
prepare = [str(python), str(root / 'scripts' / 'prepare_base_wheels.py')]
if args.wheelhouse:
    prepare += ['--wheelhouse', str(args.wheelhouse.resolve())]
subprocess.run(prepare, check=True, cwd=root)
if not (root / "src" / "workbench" / "web" / "index.html").exists():
    print("Python 环境已准备。源码开发还需 frontend 下 pnpm install 和 pnpm build；发布 wheel 已包含页面。")
else:
    print("环境已准备，运行 python start.py（SDK 另在界面离线导入）。")
