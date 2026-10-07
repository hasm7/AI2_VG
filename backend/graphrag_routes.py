"""API routes for Microsoft GraphRAG, the separate RAG system next to the graph layers.

Kept in their own blueprint so that `app.py` only registers them. The input export runs here, in the backend, because
it reads PostgreSQL (read-only, `graphrag_input.py`); everything that needs the `graphrag` library is asked of the
GraphRAG service (`graphrag_service/server.py`), which runs in its own environment. When the service is not running,
these routes say so and the rest of the app is unaffected.
"""

import json
import os
import urllib.error
import urllib.request

from flask import Blueprint, jsonify, request

from graphrag_input import ExportRunningError, export_input, input_state
from topic_event_extraction import postgres_database_url

GRAPHRAG_SERVICE_URL = os.getenv("GRAPHRAG_SERVICE_URL", "http://127.0.0.1:8100")
SERVICE_TIMEOUT_SECONDS = 3
# The first config request imports the graphrag library in the service, which takes several seconds.
CONFIG_TIMEOUT_SECONDS = 60
# A question: global and DRIFT search make many model calls and can take minutes.
QUERY_TIMEOUT_SECONDS = 600

graphrag_blueprint = Blueprint("graphrag", __name__)


def service_get(path: str, timeout: float = SERVICE_TIMEOUT_SECONDS) -> dict:
    with urllib.request.urlopen(f"{GRAPHRAG_SERVICE_URL}{path}", timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def service_call(method: str, path: str, timeout: float, body: dict | None = None) -> tuple[dict, int]:
    """Any method; the service's own status code and error message are passed on to the frontend."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    call = urllib.request.Request(f"{GRAPHRAG_SERVICE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(call, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), response.status
    except urllib.error.HTTPError as error:
        return json.loads(error.read().decode("utf-8") or "{}"), error.code
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return {"error": f"GraphRAG service not reachable: {error}"}, 503


@graphrag_blueprint.route("/api/graphrag/status", methods=["GET"])
def api_graphrag_status():
    try:
        return jsonify({"running": True, **service_get("/status")})
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return jsonify({"running": False, "url": GRAPHRAG_SERVICE_URL, "error": str(error)})


@graphrag_blueprint.route("/api/graphrag/config", methods=["GET"])
def api_graphrag_config():
    try:
        return jsonify(service_get("/config", timeout=CONFIG_TIMEOUT_SECONDS))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return jsonify({"valid": False, "error": f"GraphRAG service not reachable: {error}"}), 503


@graphrag_blueprint.route("/api/graphrag/index", methods=["GET"])
def api_graphrag_index():
    payload, status_code = service_call("GET", "/index", CONFIG_TIMEOUT_SECONDS)
    return jsonify(payload), status_code


# Starts `graphrag index` in the service and returns at once; the frontend follows the build with GET.
@graphrag_blueprint.route("/api/graphrag/index", methods=["POST", "OPTIONS"])
def api_graphrag_index_start():
    if request.method == "OPTIONS":
        return ("", 204)

    payload, status_code = service_call("POST", "/index", CONFIG_TIMEOUT_SECONDS)
    return jsonify(payload), status_code


@graphrag_blueprint.route("/api/graphrag/entities", methods=["GET"])
def api_graphrag_entities():
    payload, status_code = service_call("GET", "/entities", CONFIG_TIMEOUT_SECONDS)
    return jsonify(payload), status_code


@graphrag_blueprint.route("/api/graphrag/communities", methods=["GET"])
def api_graphrag_communities():
    payload, status_code = service_call("GET", "/communities", CONFIG_TIMEOUT_SECONDS)
    return jsonify(payload), status_code


@graphrag_blueprint.route("/api/graphrag/communities/<int:community_id>", methods=["GET"])
def api_graphrag_community_report(community_id: int):
    payload, status_code = service_call("GET", f"/communities/{community_id}", CONFIG_TIMEOUT_SECONDS)
    return jsonify(payload), status_code


# Asks GraphRAG a question (calls OpenAI, costs money); body {"method": "global"|"local"|"drift"|"basic", "question"}.
@graphrag_blueprint.route("/api/graphrag/query", methods=["POST", "OPTIONS"])
def api_graphrag_query():
    if request.method == "OPTIONS":
        return ("", 204)

    body = request.get_json(silent=True) or {}
    payload, status_code = service_call(
        "POST", "/query", QUERY_TIMEOUT_SECONDS,
        body={"method": body.get("method"), "question": body.get("question")},
    )
    return jsonify(payload), status_code


@graphrag_blueprint.route("/api/graphrag/input", methods=["GET"])
def api_graphrag_input():
    try:
        return jsonify(input_state())
    except Exception as error:  # noqa: BLE001
        return jsonify({"error": str(error)}), 500


@graphrag_blueprint.route("/api/graphrag/input/export", methods=["POST", "OPTIONS"])
def api_graphrag_input_export():
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        return jsonify(export_input(postgres_database_url()))
    except ExportRunningError as error:
        return jsonify({"error": str(error)}), 409
    except Exception as error:  # noqa: BLE001
        return jsonify({"error": str(error)}), 500
