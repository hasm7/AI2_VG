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
- GET /entities: every entity and relationship in the index.
- GET /communities: the community hierarchy (level, parent, children, report title and rating).
- GET /communities/<id>: one community's report (summary, rating, findings) and its entities.
- GET /graph: the graph to draw: every entity with a fixed position and its level 0 community, and every relationship.
These four only read the index files in output/ (the graph's positions are computed once and kept in a file there).
- POST /query {"method": "global" | "local" | "drift" | "basic", "question": "..."}: answers a question with
  Microsoft's own search (the functions `graphrag query` runs), and returns the answer and what it was built from.
  This calls OpenAI and costs money.
"""

import contextvars
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

# `graphrag query`'s functions print the answer; on a Windows console without UTF-8 a character such as ’ would
# raise UnicodeEncodeError after the (paid) answer has arrived.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

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
    search_models = {
        "global": model(config.completion_models, config.global_search.completion_model_id),
        "local": model(config.completion_models, config.local_search.completion_model_id),
        "drift": model(config.completion_models, config.drift_search.completion_model_id),
        "basic": model(config.completion_models, config.basic_search.completion_model_id),
    }
    return {
        "valid": True,
        "completion_model": model(config.completion_models, config.extract_graph.completion_model_id),
        "reasoning_effort": (completion.call_args or {}).get("reasoning_effort") if completion else None,
        "search_models": search_models,
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
    """settings.yaml and every prompt file; used only for an index built outside the app (compared by file time)."""
    prompts = sorted(path for folder in PROMPT_DIRS if folder.exists() for path in folder.glob("*.txt"))
    return [SETTINGS_FILE, *prompts]


def config_hash() -> str:
    """The fingerprint of exactly what the indexing pipeline reads, taken from GraphRAG's own loaded settings: the
    input, chunking, graph extraction, description summaries, claims, Leiden clustering, community reports and
    embeddings settings, the models those steps use (without the key), and the prompt files they name. Search
    settings (global, local, DRIFT, basic and their model) are left out: changing them never makes the index stale."""
    from graphrag.config.load_config import load_config  # imported on first use

    config = load_config(GRAPHRAG_PROJECT_DIR)
    sections = ["input", "chunking", "extract_graph", "summarize_descriptions", "extract_claims", "cluster_graph",
                "community_reports", "embed_text"]
    fingerprint = {name: getattr(config, name).model_dump(mode="json") for name in sections}

    def model_settings(models: dict, model_id: str) -> dict:
        settings = models[model_id].model_dump(mode="json")
        settings.pop("api_key", None)
        return settings

    completion_ids = {config.extract_graph.completion_model_id, config.summarize_descriptions.completion_model_id,
                      config.community_reports.completion_model_id}
    if config.extract_claims.enabled:
        completion_ids.add(config.extract_claims.completion_model_id)
    fingerprint["completion_models"] = {model_id: model_settings(config.completion_models, model_id)
                                        for model_id in sorted(completion_ids)}
    fingerprint["embedding_model"] = model_settings(config.embedding_models, config.embed_text.embedding_model_id)

    prompt_paths = [config.extract_graph.prompt, config.summarize_descriptions.prompt,
                    config.community_reports.graph_prompt, config.community_reports.text_prompt]
    if config.extract_claims.enabled:
        prompt_paths.append(config.extract_claims.prompt)
    fingerprint["prompts"] = {
        str(path): hashlib.sha256((GRAPHRAG_PROJECT_DIR / path).read_bytes()).hexdigest()
        for path in sorted({str(path) for path in prompt_paths if path})
        if (GRAPHRAG_PROJECT_DIR / path).exists()
    }
    return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode("utf-8")).hexdigest()


def adopt_existing_index() -> None:
    """An index built outside the app has no record of what it was built from. When neither the input nor any
    setting or prompt file has changed since it was built (compared by file time), the current fingerprints are the
    ones it was built from: record them once, so that from then on the index is compared like one built here."""
    if INDEX_RUN_FILE.exists() or not STATS_FILE.exists():
        return
    built_at = STATS_FILE.stat().st_mtime
    if INPUT_FILE.exists() and INPUT_FILE.stat().st_mtime > built_at:
        return
    if any(path.exists() and path.stat().st_mtime > built_at for path in config_files()):
        return
    built_iso = datetime.fromtimestamp(built_at, tz=timezone.utc).isoformat()
    INDEX_RUN_FILE.write_text(json.dumps({
        "status": "finished",
        "input_hash": file_hash(INPUT_FILE),
        "config_hash": config_hash(),
        "started_at": None,
        "finished_at": built_iso,
        "recorded_from_existing_build": True,
    }, indent=2), encoding="utf-8")
    print("Recorded the existing index (built outside the app) with its input and settings fingerprints.", flush=True)


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
        record = json.loads(INDEX_RUN_FILE.read_text(encoding="utf-8")) if INDEX_RUN_FILE.exists() else {}
        built = {
            "built_at": built_at.isoformat(),
            "built_by_app": bool(record) and not record.get("recorded_from_existing_build"),
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


def _read_output(name: str, columns: list[str] | None = None):
    import pandas as pd  # imported on first use

    path = OUTPUT_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError("There is no index yet. Build it in the Index (Build) tab.")
    return pd.read_parquet(path, columns=columns)


def entities_and_relationships() -> dict:
    entities = _read_output("entities", ["title", "type", "description", "degree", "frequency"])
    relationships = _read_output("relationships", ["source", "target", "description", "weight"])
    return {
        "entities": [
            {"title": row.title, "type": row.type, "description": row.description or "",
             "degree": int(row.degree), "frequency": int(row.frequency)}
            for row in entities.sort_values("degree", ascending=False).itertuples()
        ],
        "relationships": [
            {"source": row.source, "target": row.target, "description": row.description or "",
             "weight": float(row.weight)}
            for row in relationships.sort_values("weight", ascending=False).itertuples()
        ],
    }


# How many of a community's most connected entities are listed under its title in the tree.
TOP_ENTITIES_PER_COMMUNITY = 5


def community_tree() -> dict:
    communities = _read_output("communities", ["community", "level", "parent", "children", "size", "entity_ids"])
    reports = _read_output("community_reports", ["community", "title", "rank"]).set_index("community")
    entities = _read_output("entities", ["id", "title", "type", "degree"]).set_index("id")

    def top_entities(entity_ids) -> list[dict]:
        members = entities.loc[[entity_id for entity_id in entity_ids if entity_id in entities.index]]
        return [{"title": row.title, "type": row.type}
                for row in members.sort_values("degree", ascending=False).head(TOP_ENTITIES_PER_COMMUNITY).itertuples()]

    return {
        "communities": [
            {
                "community": int(row.community),
                "level": int(row.level),
                "parent": int(row.parent),
                "children": [int(child) for child in row.children],
                "size": int(row.size),
                "title": reports.at[row.community, "title"] if row.community in reports.index else None,
                "rating": float(reports.at[row.community, "rank"]) if row.community in reports.index else None,
                "top_entities": top_entities(row.entity_ids),
            }
            for row in communities.sort_values(["level", "community"]).itertuples()
        ],
    }


def community_report(community_id: int) -> dict:
    reports = _read_output("community_reports")
    match = reports[reports["community"] == community_id]
    if match.empty:
        raise FileNotFoundError(f"No report for community {community_id}.")
    report = match.iloc[0]

    communities = _read_output("communities", ["community", "entity_ids"])
    entity_ids = set(communities[communities["community"] == community_id].iloc[0]["entity_ids"])
    entities = _read_output("entities", ["id", "title", "type", "degree"])
    members = entities[entities["id"].isin(entity_ids)].sort_values("degree", ascending=False)
    return {
        "community": community_id,
        "level": int(report["level"]),
        "title": report["title"],
        "summary": report["summary"],
        "rating": float(report["rank"]),
        "rating_explanation": report["rating_explanation"],
        "findings": [{"summary": finding["summary"], "explanation": finding["explanation"]}
                     for finding in report["findings"]],
        "entities": [{"title": row.title, "type": row.type, "degree": int(row.degree)}
                     for row in members.itertuples()],
    }


# --- The graph to draw ---------------------------------------------------------------------------------------------
# Drawing hundreds of nodes is slow when the browser has to find their places itself (a force layout that moves every
# node until it settles). Here the places are computed once, in the service, and kept in a file next to the index:
# the browser only puts each node where it is told. Nodes of the same level 0 community are laid out together, as one
# cluster, and the clusters are placed side by side, largest first, so the communities can be seen in the picture.
# The file is computed again when the index is rebuilt (stats.json changes).

GRAPH_LAYOUT_FILE = OUTPUT_DIR / "app_graph_layout.json"
GRAPH_LAYOUT_VERSION = 1
# Size of a cluster: its radius grows with the square root of its number of nodes, so area follows node count.
CLUSTER_RADIUS_PER_SQRT_NODE = 42
CLUSTER_GAP = 60
CLUSTERS_ROW_WIDTH = 2400
_graph_lock = threading.Lock()


def _compute_graph() -> dict:
    import math

    import networkx as nx

    entities = _read_output("entities", ["id", "human_readable_id", "title", "type", "degree"])
    relationships = _read_output("relationships", ["source", "target", "weight"])
    communities = _read_output("communities", ["community", "level", "entity_ids"])
    reports = _read_output("community_reports", ["community", "title"]).set_index("community")

    node_by_title = {row.title: int(row.human_readable_id) for row in entities.itertuples()}
    community_of: dict[str, int] = {}
    level0 = communities[communities["level"] == 0]
    for row in level0.itertuples():
        for entity_id in row.entity_ids:
            community_of[entity_id] = int(row.community)

    edges = [
        {"source": node_by_title[row.source], "target": node_by_title[row.target], "weight": float(row.weight)}
        for row in relationships.itertuples()
        if row.source in node_by_title and row.target in node_by_title
    ]

    # One cluster per level 0 community; entities outside every community (Leiden works on the connected graph) are
    # put in a cluster of their own.
    members: dict[int, list] = {}
    for row in entities.itertuples():
        members.setdefault(community_of.get(row.id, -1), []).append(row)

    positions: dict[int, tuple[float, float]] = {}
    clusters = sorted(members.items(), key=lambda item: -len(item[1]))
    cursor_x = cursor_y = row_height = 0.0
    cluster_info = []
    for community, rows in clusters:
        ids = [int(row.human_readable_id) for row in rows]
        radius = CLUSTER_RADIUS_PER_SQRT_NODE * math.sqrt(len(ids))
        graph = nx.Graph()
        graph.add_nodes_from(ids)
        id_set = set(ids)
        for edge in edges:
            if edge["source"] in id_set and edge["target"] in id_set:
                graph.add_edge(edge["source"], edge["target"], weight=edge["weight"])
        local = nx.spring_layout(graph, seed=42, weight="weight", iterations=200) if len(ids) > 1 else {ids[0]: (0, 0)}

        if cursor_x + 2 * radius > CLUSTERS_ROW_WIDTH and cursor_x > 0:
            cursor_x, cursor_y, row_height = 0.0, cursor_y + row_height + CLUSTER_GAP, 0.0
        center_x, center_y = cursor_x + radius, cursor_y + radius
        for node, (x, y) in local.items():
            positions[node] = (round(center_x + float(x) * radius, 1), round(center_y + float(y) * radius, 1))
        cursor_x += 2 * radius + CLUSTER_GAP
        row_height = max(row_height, 2 * radius)
        cluster_info.append({
            "community": community,
            "title": reports.at[community, "title"] if community in reports.index else "Not in a community",
            "size": len(ids),
        })

    nodes = [
        {"id": int(row.human_readable_id), "title": row.title, "type": row.type, "degree": int(row.degree),
         "community": community_of.get(row.id, -1),
         "x": positions[int(row.human_readable_id)][0], "y": positions[int(row.human_readable_id)][1]}
        for row in entities.itertuples()
    ]
    return {"version": GRAPH_LAYOUT_VERSION, "index_built_at": STATS_FILE.stat().st_mtime,
            "communities": cluster_info, "nodes": nodes, "edges": edges}


def graph_for_drawing() -> dict:
    if not STATS_FILE.exists():
        raise FileNotFoundError("There is no index yet. Build it in the Index (Build) tab.")
    with _graph_lock:
        if GRAPH_LAYOUT_FILE.exists():
            cached = json.loads(GRAPH_LAYOUT_FILE.read_text(encoding="utf-8"))
            if (cached.get("version") == GRAPH_LAYOUT_VERSION
                    and cached.get("index_built_at") == STATS_FILE.stat().st_mtime):
                return cached
        graph = _compute_graph()
        GRAPH_LAYOUT_FILE.write_text(json.dumps(graph), encoding="utf-8")
        return graph


# The defaults of `graphrag query` (graphrag/cli/main.py), so a question asked here is answered as on the command line.
QUERY_COMMUNITY_LEVEL = 2
QUERY_RESPONSE_TYPE = "Multiple Paragraphs"
QUERY_METHODS = ("global", "local", "drift", "basic")
MAX_QUESTION_CHARS = 2000
# What is shown of the context a search used: at most this many rows per table, each text cut to this length.
CONTEXT_ROWS = 30
CONTEXT_TEXT_CHARS = 400
CONTEXT_COLUMNS = ["id", "title", "entity", "source", "target", "description", "text", "content", "rank", "weight",
                   "occurrence weight", "number of relationships", "in_context"]


class QueryInputError(ValueError):
    pass


class QueryRunningError(RuntimeError):
    pass


class BudgetExceededError(RuntimeError):
    """Raised when a question would pass its cost limit. The name is on graphrag_llm's list of exceptions that are
    never retried (graphrag_llm/retry/exceptions_to_skip.py), so the search stops at once instead of retrying."""


# The most one question may cost. Before every model call the service prices the call's input (counted locally with
# the tokenizer, no model call) and refuses the call when what was spent plus what is already reserved plus this call
# would pass the limit. Calls sent at the same time (global search) each reserve their share first, so they cannot
# all slip under the limit together. Output tokens are only known afterwards; they are added when the answer comes.
QUERY_COST_LIMIT_USD = 0.10


# --- What a question costs -------------------------------------------------------------------------------------------
# Microsoft's search calls the model through litellm (graphrag_llm: litellm.acompletion / litellm.aembedding /
# litellm.embedding, looked up on the module at every call). The service wraps those functions once and adds up the
# tokens of every call made while a question is answered, priced with litellm's own price list. A streamed answer only
# carries its token counts when asked for (stream_options include_usage); the extra chunk with the counts has no
# choices and is not passed on to Microsoft's code. Counting must never make a question fail: any error in it only
# marks the cost as incomplete.

_query_usage: contextvars.ContextVar = contextvars.ContextVar("graphrag_query_usage", default=None)
_usage_counter_installed = False


def _add_usage(model: str, prompt_tokens: int, completion_tokens: int, reasoning_tokens: int, embedding: bool) -> None:
    usage = _query_usage.get()
    if usage is None:
        return
    try:
        import litellm

        name = model.split("/", 1)[-1]
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=name, custom_llm_provider="openai", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        )
        cost = prompt_cost + completion_cost
    except Exception:  # noqa: BLE001 - an unknown price leaves the cost incomplete, not the question failed
        cost = None
    with usage["lock"]:
        if embedding:
            usage["embedding_calls"] += 1
            usage["embedding_tokens"] += prompt_tokens
        else:
            usage["model_calls"] += 1
            usage["prompt_tokens"] += prompt_tokens
            usage["completion_tokens"] += completion_tokens
            usage["reasoning_tokens"] += reasoning_tokens
        if cost is None:
            usage["complete"] = False
        else:
            usage["cost_usd"] += cost


def _reserve_input_cost(model: str, kwargs: dict, embedding: bool) -> float:
    """Prices a call's input before it is sent and reserves it; raises BudgetExceededError when the question's limit would
    be passed. Returns the reserved amount, to be released when the call has been counted."""
    usage = _query_usage.get()
    if usage is None:
        return 0.0
    import litellm

    name = model.split("/", 1)[-1]
    try:
        if embedding:
            inputs = kwargs.get("input") or []
            text = " ".join(inputs) if isinstance(inputs, list) else str(inputs)
            tokens = litellm.token_counter(model=name, text=text)
        else:
            tokens = litellm.token_counter(model=name, messages=kwargs.get("messages") or [])
        reserved = litellm.cost_per_token(model=name, custom_llm_provider="openai", prompt_tokens=tokens,
                                          completion_tokens=0)[0]
    except Exception:  # noqa: BLE001 - an input that cannot be priced is not reserved; the limit still counts the rest
        reserved = 0.0
    with usage["lock"]:
        if usage["cost_usd"] + usage["reserved_usd"] + reserved > QUERY_COST_LIMIT_USD:
            usage["limit_reached"] = True
            raise BudgetExceededError(
                f"Stopped: the question reached the cost limit of {QUERY_COST_LIMIT_USD:.2f} USD per question."
            )
        usage["reserved_usd"] += reserved
    return reserved


NO_CREDITS_MESSAGE = (
    "OpenAI: no credits remaining. Add credits at https://platform.openai.com/settings/organization/billing/ "
    "and ask again."
)


def _stop_if_out_of_credits(error: Exception) -> None:
    """OpenAI reports an empty account as a rate limit error (429, insufficient_quota), which graphrag retries up to
    seven times with waits of up to 128 seconds. That error never passes by waiting, so it is turned into
    BudgetExceededError, which graphrag does not retry: the question stops at once with a clear message. Ordinary
    rate limits (too many calls right now) are left alone and still retried."""
    text = str(error)
    if type(error).__name__ == "RateLimitError" and ("insufficient_quota" in text or "credit_balance_exhausted" in text):
        usage = _query_usage.get()
        if usage is not None:
            usage["no_credits"] = True
        raise BudgetExceededError(NO_CREDITS_MESSAGE) from error


def _release_reserved(reserved: float) -> None:
    usage = _query_usage.get()
    if usage is not None and reserved:
        with usage["lock"]:
            usage["reserved_usd"] = max(0.0, usage["reserved_usd"] - reserved)


def _record_response(model: str, response, embedding: bool = False) -> None:
    try:
        tokens = getattr(response, "usage", None)
        if tokens is None:
            usage = _query_usage.get()
            if usage is not None:
                usage["complete"] = False
            return
        details = getattr(tokens, "completion_tokens_details", None)
        _add_usage(model, int(getattr(tokens, "prompt_tokens", 0) or 0),
                   int(getattr(tokens, "completion_tokens", 0) or 0),
                   int(getattr(details, "reasoning_tokens", 0) or 0) if details else 0, embedding)
    except Exception:  # noqa: BLE001
        usage = _query_usage.get()
        if usage is not None:
            usage["complete"] = False


def install_usage_counter() -> None:
    global _usage_counter_installed
    if _usage_counter_installed:
        return
    import litellm

    original_acompletion = litellm.acompletion
    original_aembedding = litellm.aembedding
    original_embedding = litellm.embedding

    async def counting_acompletion(*args, **kwargs):
        model = str(kwargs.get("model", ""))
        reserved = _reserve_input_cost(model, kwargs, embedding=False)
        if not kwargs.get("stream"):
            try:
                response = await original_acompletion(*args, **kwargs)
                _record_response(model, response)
            except Exception as error:
                _stop_if_out_of_credits(error)
                raise
            finally:
                _release_reserved(reserved)
            return response

        kwargs.setdefault("stream_options", {"include_usage": True})
        try:
            stream = await original_acompletion(*args, **kwargs)
        except BaseException as error:
            _release_reserved(reserved)
            if isinstance(error, Exception):
                _stop_if_out_of_credits(error)
            raise

        async def counted_stream():
            try:
                async for chunk in stream:
                    if getattr(chunk, "usage", None) is not None:
                        _record_response(model, chunk)
                        if not getattr(chunk, "choices", None):
                            continue  # the chunk that only carries the counts
                    yield chunk
            finally:
                _release_reserved(reserved)

        return counted_stream()

    async def counting_aembedding(*args, **kwargs):
        model = str(kwargs.get("model", ""))
        reserved = _reserve_input_cost(model, kwargs, embedding=True)
        try:
            response = await original_aembedding(*args, **kwargs)
            _record_response(model, response, embedding=True)
        except Exception as error:
            _stop_if_out_of_credits(error)
            raise
        finally:
            _release_reserved(reserved)
        return response

    def counting_embedding(*args, **kwargs):
        model = str(kwargs.get("model", ""))
        reserved = _reserve_input_cost(model, kwargs, embedding=True)
        try:
            response = original_embedding(*args, **kwargs)
            _record_response(model, response, embedding=True)
        except Exception as error:
            _stop_if_out_of_credits(error)
            raise
        finally:
            _release_reserved(reserved)
        return response

    litellm.acompletion = counting_acompletion
    litellm.aembedding = counting_aembedding
    litellm.embedding = counting_embedding
    _usage_counter_installed = True


# One question at a time, so that the cost of one question is never mixed with another's.
_query_lock = threading.Lock()


def _context_tables(context) -> dict:
    """The tables a search put in front of the model (reports, entities, relationships, sources), made JSON-safe."""
    import pandas as pd  # imported on first use

    if isinstance(context, pd.DataFrame):
        context = {"context": context}
    if not isinstance(context, dict):
        return {}

    tables = {}
    for name, table in context.items():
        if not isinstance(table, pd.DataFrame) or table.empty:
            continue
        columns = [column for column in CONTEXT_COLUMNS if column in table.columns]
        rows = []
        for record in table[columns].head(CONTEXT_ROWS).to_dict(orient="records"):
            rows.append({key: (value[:CONTEXT_TEXT_CHARS] + "..." if isinstance(value, str) and
                               len(value) > CONTEXT_TEXT_CHARS else value if isinstance(value, (str, int, float, bool))
                               or value is None else str(value))
                         for key, value in record.items()})
        tables[name] = {"total_rows": int(len(table)), "columns": columns, "rows": rows}
    return tables


def run_query(body: dict) -> dict:
    method = body.get("method")
    question = (body.get("question") or "").strip()
    if method not in QUERY_METHODS:
        raise QueryInputError(f"Unknown method: {method}. Use one of {', '.join(QUERY_METHODS)}.")
    if not question:
        raise QueryInputError("The question is empty.")
    if len(question) > MAX_QUESTION_CHARS:
        raise QueryInputError(f"The question is longer than {MAX_QUESTION_CHARS} characters.")
    if not STATS_FILE.exists():
        raise FileNotFoundError("There is no index yet. Build it in the Index (Build) tab.")

    from graphrag.cli import query as graphrag_query  # Microsoft's own `graphrag query` functions

    if not _query_lock.acquire(blocking=False):
        raise QueryRunningError("A question is already being answered. Try again when it is done.")
    usage = {"lock": threading.Lock(), "model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "reasoning_tokens": 0, "embedding_calls": 0, "embedding_tokens": 0, "cost_usd": 0.0, "complete": True,
             "reserved_usd": 0.0, "limit_reached": False, "no_credits": False}
    token = _query_usage.set(usage)
    try:
        install_usage_counter()
        common = {"data_dir": None, "root_dir": GRAPHRAG_PROJECT_DIR, "response_type": QUERY_RESPONSE_TYPE,
                  "streaming": False, "query": question, "verbose": False}
        started = datetime.now(timezone.utc)
        if method == "global":
            response, context = graphrag_query.run_global_search(
                community_level=QUERY_COMMUNITY_LEVEL, dynamic_community_selection=False, **common)
        elif method == "local":
            response, context = graphrag_query.run_local_search(community_level=QUERY_COMMUNITY_LEVEL, **common)
        elif method == "drift":
            response, context = graphrag_query.run_drift_search(community_level=QUERY_COMMUNITY_LEVEL, **common)
        else:
            response, context = graphrag_query.run_basic_search(**common)
        seconds = (datetime.now(timezone.utc) - started).total_seconds()
    finally:
        _query_usage.reset(token)
        _query_lock.release()
        # Stopped by an empty OpenAI account or by the cost limit (the search may also have caught the refusal and
        # answered without that part): the question is reported as stopped, never as a full answer.
        if usage["no_credits"]:
            raise BudgetExceededError(NO_CREDITS_MESSAGE)
        if usage["limit_reached"]:
            raise BudgetExceededError(
                f"Stopped: the question reached the cost limit of {QUERY_COST_LIMIT_USD:.2f} USD per question "
                f"({usage['cost_usd']:.4f} USD spent, {usage['model_calls']} model calls). No answer is shown."
            )

    return {
        "usage": {key: (round(value, 6) if key == "cost_usd" else value)
                  for key, value in usage.items() if key != "lock"},
        "method": method,
        "question": question,
        "answer": response if isinstance(response, str) else json.dumps(response, ensure_ascii=False, default=str),
        "seconds": round(seconds, 1),
        "community_level": QUERY_COMMUNITY_LEVEL if method != "basic" else None,
        "response_type": QUERY_RESPONSE_TYPE,
        "context": _context_tables(context),
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
        routes = {
            "/status": status,
            "/config": config_summary,
            "/index": index_state,
            "/entities": entities_and_relationships,
            "/communities": community_tree,
            "/graph": graph_for_drawing,
        }
        report_match = re.fullmatch(r"/communities/(\d+)", self.path)
        if self.path not in routes and not report_match:
            self._send_json(404, {"error": f"Unknown path: {self.path}"})
            return
        try:
            if report_match:
                self._send_json(200, community_report(int(report_match.group(1))))
            else:
                self._send_json(200, routes[self.path]())
        except FileNotFoundError as error:
            self._send_json(404, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 - report any failure to the caller
            self._send_json(500, {"error": str(error)})

    def do_POST(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        if self.path == "/query":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
                self._send_json(200, run_query(body))
            except (QueryInputError, json.JSONDecodeError) as error:
                self._send_json(400, {"error": str(error)})
            except QueryRunningError as error:
                self._send_json(409, {"error": str(error)})
            except BudgetExceededError as error:
                self._send_json(402, {"error": str(error)})
            except FileNotFoundError as error:
                self._send_json(404, {"error": str(error)})
            except Exception as error:  # noqa: BLE001 - report any failure to the caller
                self._send_json(500, {"error": f"{type(error).__name__}: {error}"})
            return
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


def warm_up() -> None:
    """Loads, in the background at start, what the first question would otherwise wait for: Microsoft's search code,
    the settings, the index tables and the tokenizer. No model is called, so it costs nothing."""
    started = datetime.now(timezone.utc)
    try:
        adopt_existing_index()
    except Exception as error:  # noqa: BLE001 - the index is then compared by file time, as before
        print(f"Could not record the existing index: {error}", flush=True)
    try:
        import tiktoken
        from graphrag.cli import query as graphrag_query
        from graphrag.config.load_config import load_config

        config = load_config(GRAPHRAG_PROJECT_DIR)
        if STATS_FILE.exists():
            graphrag_query._resolve_output_files(
                config=config,
                output_list=["entities", "communities", "community_reports", "text_units", "relationships"],
            )
        tiktoken.get_encoding(config.chunking.encoding_model or "o200k_base")
        install_usage_counter()
        seconds = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"GraphRAG warm-up done in {seconds:.1f} s.", flush=True)
    except Exception as error:  # noqa: BLE001 - the service works without the warm-up
        print(f"GraphRAG warm-up skipped: {error}", flush=True)


def main() -> None:
    threading.Thread(target=warm_up, daemon=True).start()
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
