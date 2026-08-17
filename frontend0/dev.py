from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    processes = [
        subprocess.Popen([sys.executable, "-u", "server.py"], cwd=ROOT),
        subprocess.Popen([str(ROOT / "node_modules/.bin/vite")], cwd=ROOT),
    ]
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        return next((process.returncode for process in processes if process.returncode is not None), 1)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
