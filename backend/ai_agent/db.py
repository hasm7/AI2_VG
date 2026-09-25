"""Read-only Neo4j access shared by the entry step and the specialists."""

from neo4j import unit_of_work

# The display name the graph API gives a node (`graph_label` in `app.py`), so a citation can select the node there.
NODE_NAME = "coalesce(n.display_name, n.name, n.title, n.subject, n.person_key)"
NODE_LABEL = "[l IN labels(n) WHERE l <> 'Searchable'][0]"


def read(session, query: str, params: dict | None = None, timeout: float = 10.0) -> list[dict]:
    """Runs one read query with a timeout and returns plain dict rows."""
    @unit_of_work(timeout=timeout)
    def work(tx):
        return [record.data() for record in tx.run(query, params or {})]

    return session.execute_read(work)


FETCH_NODES = f"""
MATCH (n) WHERE elementId(n) IN $ids
RETURN elementId(n) AS id, {NODE_LABEL} AS label, {NODE_NAME} AS name,
       n.embedding_text AS text,
       toString(coalesce(n.occurred_at, n.version_at, n.sent_at, n.created_at)) AS at,
       n.title AS title, n.status AS status, n.summary AS summary
"""


def node_text(row: dict, max_chars: int) -> str:
    """The node's embedded text, which already carries its context from every layer; a short fallback otherwise."""
    text = row.get("text")
    if not text:
        parts = [f"{row['label']} {row['name']}"]
        for field in ("title", "status", "summary"):
            if row.get(field):
                parts.append(f"{field}: {row[field]}")
        text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " ..."
    return text


def fetch_nodes(session, ids: list[str], timeout: float) -> dict[str, dict]:
    if not ids:
        return {}
    return {row["id"]: row for row in read(session, FETCH_NODES, {"ids": ids}, timeout)}
