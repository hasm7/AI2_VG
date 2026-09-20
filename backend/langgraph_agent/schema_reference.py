"""Dense, live-introspected Neo4j schema reference for `graph_agent`'s prompt.

`GRAPH_SCHEMA_HANDOFF.md` in the repo root is prose written for a human or
an LLM doing index/traversal planning once — node counts, rationale,
constraint tables with commentary, sections on what the snapshot does not
cover. None of that helps a model write Cypher, and all of it used to be
resent on every iteration of the tool loop. This module replaces it with
just the shape: label -> properties/types, relationship type -> endpoint
label pairs and properties, and each label's uniqueness-constraint key
(what traversal tools actually match on).

Built once, from the live database, at import time — see
`REMEDIATION_ORDER_GRAPH_RAG_AGENT.md` item 1. `GRAPH_SCHEMA_HANDOFF.md`
stays in the repo as documentation; nothing here reads it.
"""

import os

from neo4j import GraphDatabase

# Bookkeeping only, already excluded from the graph API (`backend/app.py`'s
# `load_neo4j_graph`) — no reason for the agent to know about it either.
_EXCLUDED_LABELS = {"PipelineState"}

# Retrieval infrastructure written by the embedding pass (`embedding_pass.py`),
# present on almost every label. `graph_agent` never does vector search or
# needs to reason about these — that is `search_agent`'s job, over separate
# tools that never expose the raw vector. Listing them here would roughly
# double this reference's size for a property Cypher never needs to touch.
_EXCLUDED_PROPERTIES = {"embedding", "embedding_model", "embedding_source_hash", "embedded_at"}


def _neo4j_settings() -> tuple[str, str, str, str]:
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    if uri in {"neo4j://127.0.0.1:7687", "neo4j://localhost:7687"}:
        uri = uri.replace("neo4j://", "bolt://", 1)
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    return uri, user, password, database


def _py_type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list" if not value else f"list<{_py_type_name(value[0])}>"
    if isinstance(value, dict):
        return "map"
    return type(value).__name__


def _constraint_keys(session) -> dict[str, list[str]]:
    def _read(tx):
        return [dict(row) for row in tx.run("""
            SHOW CONSTRAINTS YIELD entityType, labelsOrTypes, properties
            WHERE entityType = 'NODE'
            RETURN labelsOrTypes[0] AS label, properties AS props
        """)]
    return {row["label"]: row["props"] for row in session.execute_read(_read)}


def _format_property(name: str, types: set[str]) -> str:
    # "string" is by far the most common type in this schema (see
    # GRAPH_SCHEMA_HANDOFF.md §1) — omitting it and annotating only the
    # exceptions is most of this file's size reduction versus spelling out
    # `name:string` on every one of ~150 property occurrences.
    if types == {"string"}:
        return name
    return f"{name}:{'|'.join(sorted(types))}"


def _node_lines(session, constraint_keys: dict[str, list[str]]) -> list[str]:
    def _read_labels(tx):
        return [row["label"] for row in tx.run("CALL db.labels() YIELD label RETURN label ORDER BY label")]

    lines = []
    for label in session.execute_read(_read_labels):
        if label in _EXCLUDED_LABELS:
            continue

        def _read_props(tx, label=label):
            return [row["props"] for row in tx.run(f"MATCH (n:`{label}`) RETURN properties(n) AS props")]

        prop_types: dict[str, set[str]] = {}
        for props in session.execute_read(_read_props):
            for key, value in props.items():
                if key in _EXCLUDED_PROPERTIES:
                    continue
                prop_types.setdefault(key, set()).add(_py_type_name(value))

        # The uniqueness-constraint key properties are marked with `*`
        # in-line rather than also repeated as a separate bracketed list —
        # every key property is already one of the label's own properties,
        # so a second listing would just be the same names twice.
        key_props = set(constraint_keys.get(label, []))
        props_text = ", ".join(
            ("*" if key in key_props else "") + _format_property(key, types)
            for key, types in sorted(prop_types.items())
        )
        lines.append(f"{label}: {props_text}")
    return lines


def _compress_endpoint_pairs(pairs: set[str]) -> str:
    """Collapses a fan-out of endpoint pairs to `many->X` / `X->many` when
    one side is uniform. `MENTIONS_ISSUE` alone has 8 distinct source
    labels all pointing at `Issue` — spelling every pair out is exactly the
    kind of size this reference exists to avoid, and "many source types"
    is all a query against a fixed target type actually needs to know."""
    if len(pairs) <= 3:
        return ", ".join(sorted(pairs))

    parsed = [pair.split("->", 1) for pair in pairs]
    sources = {source for source, _ in parsed}
    targets = {target for _, target in parsed}
    if len(targets) == 1:
        return f"many->{next(iter(targets))}"
    if len(sources) == 1:
        return f"{next(iter(sources))}->many"
    return ", ".join(sorted(pairs))


def _relationship_lines(session) -> list[str]:
    def _read_types(tx):
        return [row["relationshipType"] for row in tx.run(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType"
        )]

    lines = []
    for rel_type in session.execute_read(_read_types):
        def _read_endpoints(tx, rel_type=rel_type):
            return [dict(row) for row in tx.run(
                f"MATCH (a)-[r:`{rel_type}`]->(b) RETURN labels(a) AS al, labels(b) AS bl, keys(r) AS props"
            )]

        endpoint_pairs: set[str] = set()
        prop_names: set[str] = set()
        for row in session.execute_read(_read_endpoints):
            source_label = row["al"][0] if row["al"] else "?"
            target_label = row["bl"][0] if row["bl"] else "?"
            endpoint_pairs.add(f"{source_label}->{target_label}")
            prop_names.update(row["props"])

        line = f"{rel_type}: {_compress_endpoint_pairs(endpoint_pairs)}"
        if prop_names:
            line += f"; props: {', '.join(sorted(prop_names))}"
        lines.append(line)
    return lines


def build_schema_reference() -> str:
    """Connects to Neo4j and returns the dense schema text. Never raises —
    a connection failure at import time would take the whole chat agent
    down with it, so it degrades to a placeholder string instead, same
    spirit as the rest of this codebase's "surface, don't crash" pattern
    for optional dependencies."""
    uri, user, password, database = _neo4j_settings()
    if not password:
        return "(schema reference unavailable: NEO4J_PASSWORD not configured)"

    try:
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session(database=database, default_access_mode="READ") as session:
                constraint_keys = _constraint_keys(session)
                node_lines = _node_lines(session, constraint_keys)
                relationship_lines = _relationship_lines(session)
    except Exception as error:  # noqa: BLE001 - degrade, do not crash the module import
        return f"(schema reference unavailable: {error})"

    legend = "(*property = part of the uniqueness key; a property with no type shown is string)"
    return (
        "NODES " + legend + "\n" + "\n".join(node_lines)
        + "\n\nRELATIONSHIPS (from->to; \"many\" = more than 3 distinct labels observed on that side)\n"
        + "\n".join(relationship_lines)
    )
