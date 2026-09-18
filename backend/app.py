import json
import os
from datetime import date, datetime, time
from decimal import Decimal

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request
from neo4j import GraphDatabase


load_dotenv()

app = Flask(__name__)

GRAPH_SOURCE_RELATIONSHIPS = {
    "Mail": ["SENT_MAIL", "MAIL_RECIPIENT"],
    "Slack": ["SENT_SLACK_MESSAGE", "SLACK_THREAD_REPLY_TO"],
    "Teams": ["PARTICIPATED_IN_MEETING", "HAS_TEAMS_TRANSCRIPT_SEGMENT", "SPOKE_TEAMS_TRANSCRIPT_SEGMENT"],
    "Issues": ["OWNS_ISSUE", "COMMENTED_ON_ISSUE"],
    "Docs": ["AUTHORED_DOCUMENT"],
    "PRs": ["AUTHORED_PR", "REVIEWED_PR"],
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
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("BACKEND_PORT", "8000")), debug=True)
