from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = Path(__file__).resolve().parent / "dist"
INPUT = Path(os.environ.get("ASCO_PIPELINE_INPUT", ROOT / "synthetic_asco_retractionwatch_validation.xml")).resolve()
OUTPUT = ROOT / "outputs/frontend_run"
REPORT = OUTPUT / "content_integrity_results.json"
LOCK = threading.Lock()
sys.path.insert(0, str(ROOT))


def run_pipeline(input_path: Path = INPUT, output_dir: Path = OUTPUT, runner=subprocess.run) -> dict:
    if not input_path.exists():
        raise FileNotFoundError(f"Pipeline input does not exist: {input_path}")

    with tempfile.TemporaryDirectory(prefix="asco-frontend-") as temporary:
        temporary = Path(temporary)
        input_dir = input_path
        if input_path.is_file():
            input_dir = temporary / "input"
            input_dir.mkdir()
            shutil.copy2(input_path, input_dir / input_path.name)

        runner(
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/run_pipeline.py"),
                "--input-dir", str(input_dir),
                "--tortured-dictionary", str(ROOT / "🤷_tortured.csv"),
                "--output-dir", str(output_dir),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        generated_report = output_dir / "content_integrity_results.json"
        report = json.loads(generated_report.read_text(encoding="utf-8"))
        return report


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self) -> None:
        if self.path == "/api/report":
            if not REPORT.exists():
                self._json(404, {"error": "No pipeline report has been generated yet."})
                return
            self._json(200, {"report": json.loads(REPORT.read_text(encoding="utf-8"))})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/run-pipeline":
            self.send_error(404)
            return
        if not LOCK.acquire(blocking=False):
            self._json(409, {"error": "The pipeline is already running."})
            return
        try:
            self._json(200, {"report": run_pipeline()})
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            self._json(500, {"error": str(error)})
        finally:
            LOCK.release()

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    print(f"Integrity Central: http://localhost:8000 (input: {INPUT})")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
