"""Microsoft GraphRAG service: the separate process through which the app uses Microsoft's `graphrag` library.

`graphrag` 3.2.0 needs Python 3.11-3.13, while the backend runs Python 3.14 in `.venv`, so the backend cannot import it.
This small HTTP service runs in `.venv-graphrag` instead (start it with `.\\scripts\\run_graphrag_service.ps1`) and the
backend calls it under `/api/graphrag/...` (`backend/graphrag_routes.py`). It listens on 127.0.0.1 only and uses the
standard library's HTTP server, so it needs no package beyond `graphrag` itself.

It works only inside `graphrag_project/` (GraphRAG's project root: `input/`, later the settings, prompts and index
output). It never touches PostgreSQL, Neo4j or the app's own graph layers.

Endpoints:
- GET /status: the service, the `graphrag` and Python versions, and what exists in the project root.
"""

import json
import os
import platform
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRAPHRAG_PROJECT_DIR = PROJECT_ROOT / "graphrag_project"
INPUT_DIR = GRAPHRAG_PROJECT_DIR / "input"
SETTINGS_FILE = GRAPHRAG_PROJECT_DIR / "settings.yaml"
OUTPUT_DIR = GRAPHRAG_PROJECT_DIR / "output"

HOST = os.getenv("GRAPHRAG_SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("GRAPHRAG_SERVICE_PORT", "8100"))


def status() -> dict:
    input_files = sorted(path.name for path in INPUT_DIR.glob("*.jsonl")) if INPUT_DIR.exists() else []
    return {
        "service": "microsoft-graphrag",
        "graphrag_version": version("graphrag"),
        "python_version": platform.python_version(),
        "project_dir": str(GRAPHRAG_PROJECT_DIR.relative_to(PROJECT_ROOT)),
        "input_files": input_files,
        "settings_exists": SETTINGS_FILE.exists(),
        "output_exists": OUTPUT_DIR.exists() and any(OUTPUT_DIR.glob("*.parquet")),
    }


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        if self.path == "/status":
            try:
                self._send_json(200, status())
            except Exception as error:  # noqa: BLE001 - report any failure to the caller
                self._send_json(500, {"error": str(error)})
            return
        self._send_json(404, {"error": f"Unknown path: {self.path}"})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature from BaseHTTPRequestHandler
        print(f"graphrag-service: {format % args}", flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Microsoft GraphRAG service (graphrag {version('graphrag')}, Python {platform.python_version()}) "
          f"on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
