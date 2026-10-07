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

graphrag_blueprint = Blueprint("graphrag", __name__)


def service_get(path: str) -> dict:
    with urllib.request.urlopen(f"{GRAPHRAG_SERVICE_URL}{path}", timeout=SERVICE_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


@graphrag_blueprint.route("/api/graphrag/status", methods=["GET"])
def api_graphrag_status():
    try:
        return jsonify({"running": True, **service_get("/status")})
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return jsonify({"running": False, "url": GRAPHRAG_SERVICE_URL, "error": str(error)})


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
