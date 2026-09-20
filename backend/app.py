import json
import os
import subprocess
import sys
import time as time_module
import urllib.error
import urllib.request
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request, stream_with_context
from neo4j import GraphDatabase

from reference_extraction import (
    PIPELINE_STATE_LABEL,
    load_extracted_edges,
    needs_rerun,
    read_pipeline_state,
    run_extraction,
)
from topic_event_extraction import MissingApiKeyError, build_knowledge_layer, knowledge_state_payload
from embedding_pass import build_embeddings, embedding_state_payload


load_dotenv()

app = Flask(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWER_APP = PROJECT_ROOT / "viewer" / "app.py"
VIEWER_LOG = PROJECT_ROOT / "backend" / "viewer_server.log"
VIEWER_URL = "http://127.0.0.1:5000/"
viewer_process: subprocess.Popen | None = None
viewer_log_file = None

GRAPH_SOURCE_RELATIONSHIPS = {
    "Mail": ["SENT_MAIL", "MAIL_RECIPIENT"],
    "Slack": ["SENT_SLACK_MESSAGE", "SLACK_THREAD_REPLY_TO"],
    "Teams": ["PARTICIPATED_IN_MEETING", "HAS_TEAMS_TRANSCRIPT_SEGMENT", "SPOKE_TEAMS_TRANSCRIPT_SEGMENT"],
    "Issues": [
        "CREATED_ISSUE",
        "OWNS_ISSUE",
        "COMMENTED_ON_ISSUE",
        "HAS_ISSUE_VERSION",
        "NEXT_ISSUE_VERSION",
        "CHANGED_ISSUE_VERSION",
        "HAS_ISSUE_COMMENT",
        "WROTE_ISSUE_COMMENT",
        "REPLY_TO_ISSUE_COMMENT",
    ],
    "Docs": [
        "AUTHORED_DOCUMENT",
        "HAS_DOCUMENT_VERSION",
        "NEXT_DOCUMENT_VERSION",
        "AUTHORED_DOCUMENT_VERSION",
    ],
    "PRs": [
        "AUTHORED_PR",
        "REVIEWED_PR",
        "HAS_PR_REVIEW",
        "WROTE_PR_REVIEW",
        "REPLY_TO_PR_REVIEW",
        "HAS_CODE_CHANGE",
    ],
    "References": [
        "MENTIONS_ISSUE",
        "MENTIONS_PULL_REQUEST",
        "MENTIONS_DOCUMENT",
    ],
    "Knowledge": [
        "ABOUT_TOPIC",
        "DERIVED_FROM",
        "EVENT_OF_TOPIC",
        "EVIDENCED_BY",
        "CAUSED",
        "ACTED_IN_EVENT",
    ],
}


@app.after_request
def add_api_cors_headers(response):
    if request.path.startswith("/api/"):
        allowed_origins = {
            os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:5173"),
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
        origin = request.headers.get("Origin")
        response.headers["Access-Control-Allow-Origin"] = origin if origin in allowed_origins else "http://127.0.0.1:5173"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def neo4j_connection_settings() -> tuple[str, str, str, str]:
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    if uri in {"neo4j://127.0.0.1:7687", "neo4j://localhost:7687"}:
        uri = uri.replace("neo4j://", "bolt://", 1)
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    if not password:
        raise RuntimeError("NEO4J_PASSWORD is required.")

    return uri, user, password, database


def is_viewer_running() -> bool:
    global viewer_process

    if viewer_process and viewer_process.poll() is None:
        return True

    viewer_process = None
    return viewer_responds()


def viewer_responds() -> bool:
    try:
        with urllib.request.urlopen(VIEWER_URL, timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_for_viewer(timeout_seconds: float = 8) -> bool:
    deadline = time_module.monotonic() + timeout_seconds

    while time_module.monotonic() < deadline:
        if viewer_process and viewer_process.poll() is not None:
            return False
        if viewer_responds():
            return True
        time_module.sleep(0.25)

    return False


def start_viewer() -> None:
    global viewer_log_file, viewer_process

    if is_viewer_running():
        return

    if not VIEWER_APP.exists():
        raise RuntimeError(f"SQL viewer app not found: {VIEWER_APP}")

    viewer_env = os.environ.copy()
    viewer_env.pop("WERKZEUG_RUN_MAIN", None)
    viewer_env.pop("WERKZEUG_SERVER_FD", None)

    viewer_log_file = VIEWER_LOG.open("a", encoding="utf-8")
    viewer_process = subprocess.Popen(
        [sys.executable, str(VIEWER_APP)],
        cwd=PROJECT_ROOT,
        env=viewer_env,
        stdout=viewer_log_file,
        stderr=viewer_log_file,
    )

    if not wait_for_viewer():
        stop_viewer()
        raise RuntimeError(f"SQL viewer did not start on http://127.0.0.1:5000. See {VIEWER_LOG}.")


def stop_viewer() -> None:
    global viewer_log_file, viewer_process

    if not viewer_process:
        if viewer_log_file:
            viewer_log_file.close()
            viewer_log_file = None
        return

    if viewer_process.poll() is None:
        viewer_process.terminate()
        try:
            viewer_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            viewer_process.kill()
            viewer_process.wait(timeout=3)

    viewer_process = None
    if viewer_log_file:
        viewer_log_file.close()
        viewer_log_file = None


def graph_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date, time, Decimal)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def graph_properties(properties):
    return {key: graph_value(value) for key, value in dict(properties).items()}


def graph_label(labels, properties):
    props = dict(properties)
    return (
        props.get("display_name")
        or props.get("name")
        or props.get("title")
        or props.get("subject")
        or props.get("person_key")
        or (labels[0] if labels else "Node")
    )


def graph_summary(properties):
    props = dict(properties)
    return (
        props.get("title")
        or props.get("subject")
        or props.get("email")
        or props.get("channel_name")
        or props.get("description")
        or ""
    )


def graph_source_for_relationship(relationship_type):
    for source, relationship_types in GRAPH_SOURCE_RELATIONSHIPS.items():
        if relationship_type in relationship_types:
            return source
    return "All"


def load_neo4j_graph(source):
    uri, user, password, database = neo4j_connection_settings()
    source = source or "All"

    if source != "All" and source not in GRAPH_SOURCE_RELATIONSHIPS:
        abort(400, f"Unsupported graph source: {source}")

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database, default_access_mode="READ") as session:
            if source == "All":
                node_records = session.execute_read(
                    lambda tx: list(
                        tx.run(
                            """
                            MATCH (n)
                            WHERE NOT n:PipelineState
                            RETURN elementId(n) AS id,
                                   labels(n) AS labels,
                                   properties(n) AS properties
                            """
                        )
                    )
                )
                relationship_records = session.execute_read(
                    lambda tx: list(
                        tx.run(
                            """
                            MATCH (a)-[r]->(b)
                            RETURN elementId(r) AS id,
                                   elementId(a) AS source,
                                   elementId(b) AS target,
                                   type(r) AS type,
                                   properties(r) AS properties
                            """
                        )
                    )
                )
            else:
                relationship_types = GRAPH_SOURCE_RELATIONSHIPS[source]
                node_records = session.execute_read(
                    lambda tx: list(
                        tx.run(
                            """
                            MATCH (a)-[r]->(b)
                            WHERE type(r) IN $relationship_types
                            WITH collect(DISTINCT a) + collect(DISTINCT b) AS nodes
                            UNWIND nodes AS n
                            RETURN DISTINCT elementId(n) AS id,
                                   labels(n) AS labels,
                                   properties(n) AS properties
                            """,
                            relationship_types=relationship_types,
                        )
                    )
                )
                relationship_records = session.execute_read(
                    lambda tx: list(
                        tx.run(
                            """
                            MATCH (a)-[r]->(b)
                            WHERE type(r) IN $relationship_types
                            RETURN elementId(r) AS id,
                                   elementId(a) AS source,
                                   elementId(b) AS target,
                                   type(r) AS type,
                                   properties(r) AS properties
                            """,
                            relationship_types=relationship_types,
                        )
                    )
                )

    nodes = []
    for record in node_records:
        labels = record["labels"]
        properties = record["properties"]
        nodes.append(
            {
                "id": record["id"],
                "label": graph_label(labels, properties),
                "type": labels[0] if labels else "Node",
                "summary": graph_summary(properties),
                "properties": graph_properties(properties),
            }
        )

    relationships = []
    for record in relationship_records:
        relationship_type = record["type"]
        relationships.append(
            {
                "id": record["id"],
                "source": record["source"],
                "target": record["target"],
                "label": relationship_type,
                "sourceType": graph_source_for_relationship(relationship_type),
                "properties": graph_properties(record["properties"]),
            }
        )

    return {"source": source, "nodes": nodes, "relationships": relationships}


@app.route("/api/graph", methods=["GET", "OPTIONS"])
def api_graph():
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        return jsonify(load_neo4j_graph(request.args.get("source", "All")))
    except Exception as error:
        return jsonify({"error": str(error)}), 500


def reference_state_payload(session):
    state = session.execute_read(read_pipeline_state)
    return {
        "last_import_at": state["last_import_at"],
        "last_extraction_at": state["last_extraction_at"],
        "needs_rerun": needs_rerun(state),
    }


@app.route("/api/references", methods=["GET", "OPTIONS"])
def api_references():
    """Current derived reference edges plus the pipeline timestamps."""
    if request.method == "OPTIONS":
        return ("", 204)

    uri, user, password, database = neo4j_connection_settings()
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database, default_access_mode="READ") as session:
            payload = reference_state_payload(session)
            edges = session.execute_read(load_extracted_edges)

    counts = {}
    for edge in edges:
        counts[edge["relationship"]] = counts.get(edge["relationship"], 0) + 1

    payload.update({"edges": edges, "total": len(edges), "counts": counts})
    return jsonify(payload)


@app.route("/api/references/extract", methods=["POST", "OPTIONS"])
def api_references_extract():
    """Run the deterministic reference extraction pass."""
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        uri, user, password, database = neo4j_connection_settings()
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session(database=database) as session:
                result = run_extraction(session)
                result.update(reference_state_payload(session))
        return jsonify(result)
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/knowledge", methods=["GET", "OPTIONS"])
def api_knowledge():
    """Current Topic/Event knowledge layer plus the pipeline timestamps."""
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        uri, user, password, database = neo4j_connection_settings()
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session(database=database, default_access_mode="READ") as session:
                return jsonify(knowledge_state_payload(session))
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/knowledge/build", methods=["POST", "OPTIONS"])
def api_knowledge_build():
    """Run the LLM-driven Topic/Event extraction pass."""
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        uri, user, password, database = neo4j_connection_settings()
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session(database=database) as session:
                result = build_knowledge_layer(session)
        return jsonify(result)
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/embeddings", methods=["GET", "OPTIONS"])
def api_embeddings():
    """Current embedding coverage per label plus the pipeline timestamps."""
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        uri, user, password, database = neo4j_connection_settings()
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session(database=database, default_access_mode="READ") as session:
                return jsonify(embedding_state_payload(session))
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/embeddings/build", methods=["POST", "OPTIONS"])
def api_embeddings_build():
    """Run the embedding pass. Body: {"force": bool} (default false)."""
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", False))

    try:
        uri, user, password, database = neo4j_connection_settings()
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session(database=database) as session:
                result = build_embeddings(session, force=force)
        return jsonify(result)
    except MissingApiKeyError as error:
        return jsonify({"error": str(error)}), 503
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/neo4j/status", methods=["GET", "OPTIONS"])
def api_neo4j_status():
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        uri, user, password, _database = neo4j_connection_settings()
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            driver.verify_connectivity()
        return jsonify({"connected": True})
    except Exception as error:
        return jsonify({"connected": False, "error": str(error)}), 503


@app.route("/api/ai/chat", methods=["POST", "OPTIONS"])
def api_ai_chat():
    """Graph RAG agent, streamed as Server-Sent Events.

    Body: {"message": str, "thread_id": str}. Event payloads (one JSON
    object per `data:` line): {"type": "status", "text": str},
    {"type": "token", "text": str}, {"type": "sources", "citations": [...],
    "dropped_citations": [...]}, {"type": "done", "answer": str,
    "citations": [...], "dropped_citations": [...], "token_usage": {...}}.
    """
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    thread_id = str(payload.get("thread_id", "")).strip()

    if not message:
        return jsonify({"error": "Message is required."}), 400
    if not thread_id:
        return jsonify({"error": "thread_id is required."}), 400

    # Imported on demand so a missing chat dependency cannot stop the graph API
    # from starting. The agent needs langgraph and openai, which the graph API
    # does not.
    try:
        from langgraph_agent.agent import MissingApiKeyError, require_openai_client, stream_chat
    except ImportError as error:
        return jsonify({
            "error": f"Chat agent is unavailable: {error}. "
                     "Install its dependencies with .\\scripts\\install_deps.ps1."
        }), 503

    # Checked eagerly, outside the stream, so a missing key still produces a
    # plain 503 status rather than a 200 that turns into an error mid-stream
    # (the SSE response's status line is fixed the moment it is returned).
    try:
        require_openai_client()
    except MissingApiKeyError as error:
        return jsonify({"error": str(error)}), 503

    try:
        uri, user, password, database = neo4j_connection_settings()
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    def generate():
        try:
            for event in stream_chat(message, thread_id, uri, user, password, database):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except Exception as error:
            yield f"data: {json.dumps({'type': 'done', 'answer': '', 'error': str(error)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/viewer/start", methods=["POST", "OPTIONS"])
def api_viewer_start():
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        start_viewer()
        return jsonify({"url": VIEWER_URL})
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/viewer/stop", methods=["POST", "OPTIONS"])
def api_viewer_stop():
    if request.method == "OPTIONS":
        return ("", 204)

    stop_viewer()
    return jsonify({"stopped": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("BACKEND_PORT", "8000")), debug=True)
