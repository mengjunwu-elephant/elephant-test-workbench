"""Stop an owned worker and its descendants; a process exit says nothing about robot motion."""
import os
import signal
import subprocess
from .environments import creation_options


def terminate_worker(process):
    if os.name == 'nt':
        # Windows venv python.exe is a redirector: terminating it alone leaks the real worker.
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10, **creation_options())
        if result.returncode and process.poll() is None:
            raise RuntimeError('无法结束执行进程树：'+result.stderr.decode(errors='replace'))
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
