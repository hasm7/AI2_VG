"""Deterministic reference extraction over the Neo4j graph.

The six source trees share only `Person`, so the only path from a mail to a
pull request runs through a human being. But the content connections are
already written down in the text: `AUTH-17` appears in Slack, in a transcript
and in two document versions. This pass turns those strings into relationships.

There is no interpretation here. If a text contains an identifier and a node
with that identifier exists, the edge is drawn. If the identifier resolves to
nothing, the match is discarded. That discard is the safety mechanism: the
pattern for an issue key also matches `UTF-8`, and the lookup is what throws it
away.

The edges are marked `derived: true`. Everything else in the graph is a
transcript of a SQL row; these are the first thing the system concluded rather
than copied, and an answer built on them must be able to tell the difference.

This pass reads and writes Neo4j only. It never touches PostgreSQL, creates no
nodes apart from the bookkeeping singleton, and calls no model.
"""

import re
from datetime import datetime, timezone


EXTRACTOR_NAME = "reference-extraction-v1"

PIPELINE_STATE_LABEL = "PipelineState"
PIPELINE_STATE_ID = "singleton"

MENTIONS_ISSUE = "MENTIONS_ISSUE"
MENTIONS_PULL_REQUEST = "MENTIONS_PULL_REQUEST"
MENTIONS_DOCUMENT = "MENTIONS_DOCUMENT"

EXTRACTED_RELATIONSHIP_TYPES = [MENTIONS_ISSUE, MENTIONS_PULL_REQUEST, MENTIONS_DOCUMENT]

ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
PULL_REQUEST_PATTERN = re.compile(r"\b([a-z0-9][a-z0-9._-]*)#(\d+)\b")
DOCUMENT_IDENTIFIER_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]{3,}$")


# Which labels are scanned, which of their properties hold text, and how the
# node reaches the parent entity it must not link back to. Retrieval-unit nodes
# are included: a reference belongs to the comment that contains it, not to the
# parent issue. Raw JSON properties are deliberately absent, because their
# content is already covered by the retrieval-unit nodes.
SCAN_TARGETS = [
    ("MailMessage", ["subject", "body"], None),
    ("SlackMessage", ["body"], None),
    ("TeamsTranscriptSegment", ["body"], None),
    ("TeamsMeeting", ["title"], None),
    ("Issue", ["title", "description"], "self"),
    ("IssueVersion", ["title", "description", "acceptance_criteria"],
     "MATCH (parent:Issue)-[:HAS_ISSUE_VERSION]->(n)"),
    ("IssueComment", ["body"],
     "MATCH (parent:Issue)-[:HAS_ISSUE_COMMENT]->(n)"),
    ("Document", ["title", "body"], "self"),
    ("DocumentVersion", ["title", "body", "change_summary"],
     "MATCH (parent:Document)-[:HAS_DOCUMENT_VERSION]->(n)"),
    ("PullRequest", ["title", "description"], "self"),
    ("PullRequestReview", ["body"],
     "MATCH (parent:PullRequest)-[:HAS_PR_REVIEW]->(n)"),
    ("CodeChange", ["before_summary", "after_summary"],
     "MATCH (parent:PullRequest)-[:HAS_CODE_CHANGE]->(n)"),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def document_identifier(title):
    """The leading token of a document title, when it looks like an identifier.

    `REQ-AUTH-SESSION: Administrator session lifetime` yields `REQ-AUTH-SESSION`.
    A title without such a token yields nothing.
    """
    if not title:
        return None
    leading = title.split(":", 1)[0].strip()
    return leading if DOCUMENT_IDENTIFIER_PATTERN.match(leading) else None


def build_lookups(tx):
    """Build the lookup tables from the graph itself.

    Whatever is in the graph is what can be referenced. Nothing is hardcoded.
    """
    issues = {
        row["key"]: row["id"]
        for row in tx.run("""
            MATCH (i:Issue)
            WHERE i.issue_key IS NOT NULL
            RETURN i.issue_key AS key, elementId(i) AS id
        """)
    }

    pull_requests = {
        (row["repository"], int(row["pr_number"])): row["id"]
        for row in tx.run("""
            MATCH (pr:PullRequest)
            WHERE pr.repository IS NOT NULL AND pr.pr_number IS NOT NULL
            RETURN pr.repository AS repository, pr.pr_number AS pr_number,
                   elementId(pr) AS id
        """)
    }

    documents = {}
    for row in tx.run("""
        MATCH (d:Document)
        RETURN d.title AS title, elementId(d) AS id
    """):
        identifier = document_identifier(row["title"])
        if identifier:
            documents[identifier] = row["id"]

    return issues, pull_requests, documents


def load_scan_nodes(tx):
    """Every scannable node with its text properties and its parent entity."""
    nodes = []
    for label, properties, parent_clause in SCAN_TARGETS:
        if parent_clause == "self":
            query = f"""
                MATCH (n:{label})
                RETURN elementId(n) AS id, properties(n) AS props,
                       elementId(n) AS parent_id
            """
        elif parent_clause:
            query = f"""
                MATCH (n:{label})
                OPTIONAL {parent_clause}
                RETURN elementId(n) AS id, properties(n) AS props,
                       elementId(parent) AS parent_id
            """
        else:
            query = f"""
                MATCH (n:{label})
                RETURN elementId(n) AS id, properties(n) AS props,
                       null AS parent_id
            """

        for row in tx.run(query):
            props = row["props"] or {}
            nodes.append({
                "id": row["id"],
                "label": label,
                "display_name": props.get("display_name") or props.get("name") or "",
                "parent_id": row["parent_id"],
                "texts": [(name, props.get(name)) for name in properties if props.get(name)],
            })
    return nodes


def find_references(text, issues, pull_requests, documents):
    """Find every resolvable identifier in one piece of text.

    Document identifiers are resolved first and their spans are consumed, so a
    string such as `REQ-AUTH-SESSION` cannot also be offered to the issue-key
    pattern. One string never produces two edges.
    """
    found = []
    consumed = []

    for identifier, target_id in documents.items():
        for match in re.finditer(rf"\b{re.escape(identifier)}\b", text):
            consumed.append(match.span())
            found.append((MENTIONS_DOCUMENT, target_id, match.group(0)))

    def overlaps(span):
        return any(span[0] < end and start < span[1] for start, end in consumed)

    for match in ISSUE_KEY_PATTERN.finditer(text):
        if overlaps(match.span()):
            continue
        target_id = issues.get(match.group(0))
        # An unresolved match is discarded. This is what removes `UTF-8`.
        if target_id:
            found.append((MENTIONS_ISSUE, target_id, match.group(0)))

    for match in PULL_REQUEST_PATTERN.finditer(text):
        if overlaps(match.span()):
            continue
        target_id = pull_requests.get((match.group(1), int(match.group(2))))
        if target_id:
            found.append((MENTIONS_PULL_REQUEST, target_id, match.group(0)))

    return found


def collect_edges(nodes, issues, pull_requests, documents):
    """Turn the scanned nodes into the edges that should exist."""
    edges = []
    seen = set()

    for node in nodes:
        for property_name, text in node["texts"]:
            for relationship, target_id, matched_text in find_references(
                text, issues, pull_requests, documents
            ):
                # Self-reference rule: a comment on AUTH-17 saying "AUTH-17"
                # adds nothing, because HAS_ISSUE_COMMENT already says it.
                if target_id == node["parent_id"]:
                    continue

                key = (node["id"], relationship, target_id)
                if key in seen:
                    continue
                seen.add(key)

                edges.append({
                    "source_id": node["id"],
                    "source_label": node["label"],
                    "source_display_name": node["display_name"],
                    "relationship": relationship,
                    "target_id": target_id,
                    "matched_text": matched_text,
                    "source_property": property_name,
                })

    return edges


def delete_extracted_relationships(tx):
    result = tx.run("""
        MATCH ()-[r]->()
        WHERE r.extracted_by = $extracted_by
        DELETE r
        RETURN count(r) AS deleted
    """, {"extracted_by": EXTRACTOR_NAME})
    return result.single()["deleted"]


def write_edges(tx, edges, extracted_at):
    """Create the edges, one relationship type at a time.

    The type cannot be a parameter in Cypher, and only the three constants in
    this module are ever used.
    """
    for relationship in EXTRACTED_RELATIONSHIP_TYPES:
        batch = [edge for edge in edges if edge["relationship"] == relationship]
        if not batch:
            continue
        tx.run(f"""
            UNWIND $edges AS edge
            MATCH (source) WHERE elementId(source) = edge.source_id
            MATCH (target) WHERE elementId(target) = edge.target_id
            MERGE (source)-[r:{relationship}]->(target)
            SET r.derived = true,
                r.extracted_by = $extracted_by,
                r.matched_text = edge.matched_text,
                r.source_property = edge.source_property,
                r.extracted_at = $extracted_at
        """, {"edges": batch, "extracted_by": EXTRACTOR_NAME, "extracted_at": extracted_at})


def touch_pipeline_state(tx, field, value):
    tx.run(f"""
        MERGE (s:{PIPELINE_STATE_LABEL} {{id: $id}})
        SET s.{field} = $value
    """, {"id": PIPELINE_STATE_ID, "value": value})


def read_pipeline_state(tx):
    row = tx.run(f"""
        MATCH (s:{PIPELINE_STATE_LABEL} {{id: $id}})
        RETURN s.last_import_at AS last_import_at,
               s.last_extraction_at AS last_extraction_at
    """, {"id": PIPELINE_STATE_ID}).single()
    if not row:
        return {"last_import_at": None, "last_extraction_at": None}
    return {"last_import_at": row["last_import_at"],
            "last_extraction_at": row["last_extraction_at"]}


def needs_rerun(state):
    """True when an import has happened since the last extraction."""
    if not state.get("last_extraction_at"):
        return True
    if not state.get("last_import_at"):
        return False
    return state["last_import_at"] > state["last_extraction_at"]


def load_extracted_edges(tx):
    """The edges currently in the graph, for the results table."""
    return [dict(row) for row in tx.run("""
        MATCH (source)-[r]->(target)
        WHERE r.extracted_by = $extracted_by
        RETURN labels(source)[0] AS source_label,
               coalesce(source.display_name, source.name, '') AS source_display_name,
               type(r) AS relationship,
               r.matched_text AS matched_text,
               r.source_property AS source_property,
               coalesce(target.display_name, target.name, '') AS target_display_name,
               r.extracted_at AS extracted_at
        ORDER BY source_label, source_display_name, relationship, matched_text
    """, {"extracted_by": EXTRACTOR_NAME})]


def run_extraction(session):
    """Delete the previous run's edges, then rebuild them from scratch."""
    extracted_at = now_iso()

    issues, pull_requests, documents = session.execute_read(build_lookups)
    nodes = session.execute_read(load_scan_nodes)
    edges = collect_edges(nodes, issues, pull_requests, documents)

    deleted = session.execute_write(delete_extracted_relationships)
    session.execute_write(write_edges, edges, extracted_at)
    session.execute_write(touch_pipeline_state, "last_extraction_at", extracted_at)

    counts = {name: 0 for name in EXTRACTED_RELATIONSHIP_TYPES}
    for edge in edges:
        counts[edge["relationship"]] += 1

    return {
        "extracted_at": extracted_at,
        "deleted": deleted,
        "total": len(edges),
        "counts": counts,
        "scanned_nodes": len(nodes),
        "lookups": {
            "issues": len(issues),
            "pull_requests": len(pull_requests),
            "documents": len(documents),
        },
        "edges": session.execute_read(load_extracted_edges),
    }
