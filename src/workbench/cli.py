from __future__ import annotations

import argparse
import getpass
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="elephant-pytest 本机测试工作台 / 局域网用例服务")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="启动本机工作台，不自动连接设备")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--data", type=Path)
    run.add_argument("--open", action="store_true", help="服务就绪后打开浏览器")
    hub = sub.add_parser("hub", help="启动只共享用例的独立服务")
    hub.add_argument("--host", default="127.0.0.1")
    hub.add_argument("--port", type=int, default=8766)
    hub.add_argument("--data", type=Path, required=True)
    user = sub.add_parser("hub-user", help="在服务数据目录创建账号，交互输入密码")
    user.add_argument("--data", type=Path, required=True)
    user.add_argument("--username", required=True)
    user.add_argument("--role", choices=["tester", "maintainer"], default="tester")
    args = parser.parse_args()
    if args.command == "hub-user":
        from .hub import add_user
        from .storage import Store
        password = getpass.getpass("请输入密码（至少10字符）：")
        if getpass.getpass("再次输入：") != password:
            raise SystemExit("两次密码不一致")
        add_user(Store(args.data), args.username, password, args.role)
        print(f"已创建 {args.username}（{args.role}）")
        return
    import uvicorn
    if args.command == "hub":
        from .hub import create_hub
        app = create_hub(args.data)
    else:
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            raise SystemExit("本机工作台只绑定回环地址；团队共享请单独启动 hub，不暴露设备控制接口")
        from .api import create_app
        app = create_app(args.data)
        if args.open:
            def opener():
                url = f"http://127.0.0.1:{args.port}"
                for _ in range(50):
                    try:
                        urllib.request.urlopen(url+"/api/health", timeout=0.5).close()
                        webbrowser.open(url)
                        return
                    except OSError:
                        time.sleep(0.2)
            threading.Thread(target=opener, daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=8)


if __name__ == "__main__":
    main()
