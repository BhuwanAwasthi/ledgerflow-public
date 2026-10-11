"""`make dev`: API on :8000 (uvicorn --reload) + Vite on :5173, without shell backgrounding.

Run from backend/ (the Makefile does `uv --directory backend run python scripts/dev.py`).
Ctrl+C stops both.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"


def main() -> int:
    procs: list[subprocess.Popen[bytes]] = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"], cwd=BACKEND
        )
    ]
    npm = shutil.which("npm")
    if (FRONTEND / "package.json").exists() and npm:
        procs.append(subprocess.Popen([npm, "run", "dev", "--", "--port", "5173"], cwd=FRONTEND))
    else:
        print("frontend/ not found (or npm missing): starting the API only")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return max((p.returncode or 0) for p in procs)


if __name__ == "__main__":
    sys.exit(main())
