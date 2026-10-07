"""Microsoft GraphRAG service: the separate process through which the app uses Microsoft's `graphrag` library.

`graphrag` 3.2.0 needs Python 3.11-3.13, while the backend runs Python 3.14 in `.venv`, so the backend cannot import it.
This small HTTP service runs in `.venv-graphrag` instead (start it with `.\\scripts\\run_graphrag_service.ps1`) and the
backend calls it under `/api/graphrag/...` (`backend/graphrag_routes.py`). It listens on 127.0.0.1 only and uses the
standard library's HTTP server, so it needs no package beyond `graphrag` itself.

It works only inside `graphrag_project/` (GraphRAG's project root: `input/`, later the settings, prompts and index
output). It never touches PostgreSQL, Neo4j or the app's own graph layers.

The OpenAI key comes from the project's `.env` (`OPENAI_API_KEY`, which `settings.yaml` names), loaded at start; no
second file with secrets is kept in `graphrag_project/`.

Endpoints:
- GET /status: the service, the `graphrag` and Python versions, and what exists in the project root.
- GET /config: `settings.yaml` loaded and validated by GraphRAG's own loader, summarised (never the key).
- GET /index: the index in `output/` (when it was built, from which input, what it holds) and the running build.
- POST /index: starts `graphrag index` (Microsoft's own indexing command) in the background; one build at a time.
"""

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
GRAPHRAG_PROJECT_DIR = PROJECT_ROOT / "graphrag_project"
INPUT_DIR = GRAPHRAG_PROJECT_DIR / "input"
INPUT_FILE = INPUT_DIR / "documents.jsonl"
SETTINGS_FILE = GRAPHRAG_PROJECT_DIR / "settings.yaml"
OUTPUT_DIR = GRAPHRAG_PROJECT_DIR / "output"
STATS_FILE = OUTPUT_DIR / "stats.json"
# Written by this service for every build it starts: which input and settings it was built from, and whether it
# finished. A build is refused when neither has changed since the last finished build.
INDEX_RUN_FILE = OUTPUT_DIR / "app_index_run.json"
PROMPT_DIRS = [GRAPHRAG_PROJECT_DIR / "prompts", GRAPHRAG_PROJECT_DIR / "prompts_tuned"]
INDEX_LOG = GRAPHRAG_PROJECT_DIR / "logs" / "indexing-engine.log"
INDEX_CONSOLE_LOG = GRAPHRAG_PROJECT_DIR / "logs" / "index-console.log"

# The standard pipeline of graphrag 3.2.0 (graphrag/index/workflows/factory.py), in order, for the progress display.
INDEX_WORKFLOWS = [
    "load_input_documents",
    "create_base_text_units",
    "create_final_documents",
    "extract_graph",
    "finalize_graph",
    "extract_covariates",
    "create_communities",
    "create_final_text_units",
    "create_community_reports",
    "generate_text_embeddings",
]

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


def config_summary() -> dict:
    """Loads `settings.yaml` exactly as `graphrag index` will (GraphRAG's loader validates every field) and returns
    the settings that decide how the graph is built and searched."""
    if not SETTINGS_FILE.exists():
        return {"valid": False, "error": "settings.yaml not found. Run graphrag init first."}

    from graphrag.config.load_config import load_config  # imported on first use: graphrag is slow to import

    try:
        config = load_config(GRAPHRAG_PROJECT_DIR)
    except Exception as error:  # noqa: BLE001 - a configuration error is reported, not raised
        return {"valid": False, "error": str(error)}

    def model(models: dict, model_id: str) -> str:
        entry = models.get(model_id)
        return f"{entry.model_provider}/{entry.model}" if entry else f"(missing: {model_id})"

    completion = config.completion_models.get(config.extract_graph.completion_model_id)
    return {
        "valid": True,
        "completion_model": model(config.completion_models, config.extract_graph.completion_model_id),
        "reasoning_effort": (completion.call_args or {}).get("reasoning_effort") if completion else None,
        "embedding_model": model(config.embedding_models, config.embed_text.embedding_model_id),
        "input": {
            "type": config.input.type,
            "base_dir": config.input_storage.base_dir,
            "id_column": config.input.id_column,
            "title_column": config.input.title_column,
            "text_column": config.input.text_column,
        },
        "chunking": {
            "type": config.chunking.type,
            "size": config.chunking.size,
            "overlap": config.chunking.overlap,
            "encoding_model": config.chunking.encoding_model,
            "prepend_metadata": config.chunking.prepend_metadata,
        },
        "extract_graph": {
            "entity_types": config.extract_graph.entity_types,
            "max_gleanings": config.extract_graph.max_gleanings,
        },
        "max_cluster_size": config.cluster_graph.max_cluster_size,
        "extract_claims": config.extract_claims.enabled,
        "community_report_max_length": config.community_reports.max_length,
        "vector_store": config.vector_store.type,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path) -> str | None:
    """The same fingerprint as the backend's export (`content_hash` in backend/graphrag_input.py)."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def config_files() -> list[Path]:
    """Everything besides the input that decides what the index holds: settings.yaml and every prompt file."""
    prompts = sorted(path for folder in PROMPT_DIRS if folder.exists() for path in folder.glob("*.txt"))
    return [SETTINGS_FILE, *prompts]


def config_hash() -> str:
    digest = hashlib.sha256()
    for path in config_files():
        if path.exists():
            digest.update(str(path.relative_to(GRAPHRAG_PROJECT_DIR)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def index_changes() -> list[str]:
    """Why the index needs a build; empty when it is up to date with the input and the settings."""
    if not STATS_FILE.exists():
        return ["There is no index yet."]

    record = json.loads(INDEX_RUN_FILE.read_text(encoding="utf-8")) if INDEX_RUN_FILE.exists() else None
    if record:
        # Started from the app: the fingerprints of the input and the settings it was built from are recorded.
        if record.get("status") != "finished":
            return ["The last build did not finish."]
        changes = []
        if file_hash(INPUT_FILE) != record.get("input_hash"):
            changes.append("The input documents have changed since the last build.")
        if config_hash() != record.get("config_hash"):
            changes.append("settings.yaml or a prompt has changed since the last build.")
        return changes

    # Built outside the app (no record): compare the files' times with the time of the build.
    built_at = STATS_FILE.stat().st_mtime
    changes = []
    if INPUT_FILE.exists() and INPUT_FILE.stat().st_mtime > built_at:
        changes.append("The input documents have changed since the last build.")
    if any(path.exists() and path.stat().st_mtime > built_at for path in config_files()):
        changes.append("settings.yaml or a prompt has changed since the last build.")
    return changes


# One build at a time. `_index_run` is the latest build started by this service (kept in memory).
_index_lock = threading.Lock()
_index_run: dict = {}


class IndexRunningError(RuntimeError):
    pass


class IndexUpToDateError(RuntimeError):
    pass


def start_index() -> dict:
    if not _index_lock.acquire(blocking=False):
        raise IndexRunningError("An index build is already running.")
    try:
        if not INPUT_FILE.exists():
            raise FileNotFoundError("No input documents. Export them in the Input tab first.")
        if not index_changes():
            raise IndexUpToDateError("The index is up to date: nothing has changed since the last build.")
    except Exception:
        _index_lock.release()
        raise

    _index_run.clear()
    _index_run.update({
        "running": True,
        "started_at": now_iso(),
        "input_hash": file_hash(INPUT_FILE),
        "config_hash": config_hash(),
        "log_offset": INDEX_LOG.stat().st_size if INDEX_LOG.exists() else 0,
    })
    # Recorded before the build starts: a build that is stopped half-way is then known not to have finished.
    _write_run_record("running")
    threading.Thread(target=_run_index, daemon=True).start()
    return index_state()


def _write_run_record(status_value: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_RUN_FILE.write_text(json.dumps({
        "status": status_value,
        "input_hash": _index_run["input_hash"],
        "config_hash": _index_run["config_hash"],
        "started_at": _index_run["started_at"],
        "finished_at": _index_run.get("finished_at"),
    }, indent=2), encoding="utf-8")


def _run_index() -> None:
    """Runs Microsoft's own command, `graphrag index`, as a child process, so the build is exactly the CLI's."""
    try:
        INDEX_CONSOLE_LOG.parent.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        with INDEX_CONSOLE_LOG.open("w", encoding="utf-8") as console:
            completed = subprocess.run(
                [sys.executable, "-m", "graphrag", "index", "--root", str(GRAPHRAG_PROJECT_DIR)],
                cwd=GRAPHRAG_PROJECT_DIR, env=environment, stdin=subprocess.DEVNULL, stdout=console,
                stderr=subprocess.STDOUT, check=False,
            )
        _index_run["exit_code"] = completed.returncode
        if completed.returncode != 0:
            _index_run["error"] = f"graphrag index stopped with exit code {completed.returncode}. See {INDEX_CONSOLE_LOG.name}."
    except Exception as error:  # noqa: BLE001 - reported in the index state
        _index_run["error"] = str(error)
    finally:
        _index_run["finished_at"] = now_iso()
        _index_run["running"] = False
        try:
            _write_run_record("failed" if _index_run.get("error") else "finished")
        finally:
            _index_lock.release()


def _run_progress() -> dict:
    """Which workflows of the current (or latest) build have started and completed, read from GraphRAG's own log."""
    if not INDEX_LOG.exists():
        return {"completed_workflows": [], "current_workflow": None, "errors": []}
    with INDEX_LOG.open("r", encoding="utf-8", errors="replace") as log:
        log.seek(_index_run.get("log_offset", 0))
        text = log.read()
    started = re.findall(r"Workflow started: (\w+)", text)
    completed = re.findall(r"Workflow completed: (\w+)", text)
    errors = [line.strip()[:300] for line in text.splitlines() if " - ERROR - " in line][-5:]
    current = next((name for name in reversed(started) if name not in completed), None)
    return {"completed_workflows": completed, "current_workflow": current, "errors": errors}


def _output_counts() -> dict:
    """Rows in each output table, and communities per level."""
    import pyarrow.parquet as parquet  # imported on first use

    tables = {
        "documents": "documents.parquet",
        "text_units": "text_units.parquet",
        "entities": "entities.parquet",
        "relationships": "relationships.parquet",
        "communities": "communities.parquet",
        "community_reports": "community_reports.parquet",
    }
    counts = {name: parquet.read_metadata(OUTPUT_DIR / file).num_rows
              for name, file in tables.items() if (OUTPUT_DIR / file).exists()}
    communities_file = OUTPUT_DIR / "communities.parquet"
    if communities_file.exists():
        levels = parquet.read_table(communities_file, columns=["level"]).column("level").to_pylist()
        counts["communities_per_level"] = {str(level): levels.count(level) for level in sorted(set(levels))}
    return counts


def index_state() -> dict:
    built = None
    if STATS_FILE.exists():
        stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        built_at = datetime.fromtimestamp(STATS_FILE.stat().st_mtime, tz=timezone.utc)
        built = {
            "built_at": built_at.isoformat(),
            "built_by_app": INDEX_RUN_FILE.exists(),
            "total_runtime_seconds": round(stats.get("total_runtime", 0)),
            "workflow_seconds": {name: round(values.get("overall", 0), 1)
                                 for name, values in stats.get("workflows", {}).items()},
            "counts": _output_counts(),
        }

    run = None
    if _index_run:
        run = {key: value for key, value in _index_run.items()
               if key not in {"log_offset", "input_hash", "config_hash"}}
        run.update(_run_progress())
    # While a build runs, nothing is "changed": the build itself is what brings the index up to date.
    changes = [] if _index_run.get("running") else index_changes()
    return {"workflows": INDEX_WORKFLOWS, "index": built, "run": run, "changes": changes, "up_to_date": not changes}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        routes = {"/status": status, "/config": config_summary, "/index": index_state}
        if self.path not in routes:
            self._send_json(404, {"error": f"Unknown path: {self.path}"})
            return
        try:
            self._send_json(200, routes[self.path]())
        except Exception as error:  # noqa: BLE001 - report any failure to the caller
            self._send_json(500, {"error": str(error)})

    def do_POST(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        if self.path != "/index":
            self._send_json(404, {"error": f"Unknown path: {self.path}"})
            return
        try:
            self._send_json(202, start_index())
        except (IndexRunningError, IndexUpToDateError) as error:
            self._send_json(409, {"error": str(error)})
        except FileNotFoundError as error:
            self._send_json(400, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 - report any failure to the caller
            self._send_json(500, {"error": str(error)})

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
