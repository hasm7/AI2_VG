"""Tool schemas and executors for the graph RAG agent.

Schemas are static JSON-schema dicts passed to `client.responses.create(tools=...)`
and never depend on a live session. Executors are built per request, bound
to that request's Neo4j session and OpenAI client, since they must run real
queries and real embedding calls. Keeping schema and executor separate is
what lets the schemas stay byte-stable across every call
(`WORK_ORDER_GRAPH_RAG_AGENT.md` section 2) while the executors still see
fresh state each request.
"""

import re
import sys
from pathlib import Path
from typing import Callable

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from embedding_pass import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, LABELS as EMBEDDING_LABELS
from cypher_guard import CypherWriteRejected, QUERY_TIMEOUT_SECONDS, enforce_read_only

MAX_K = 20
DEFAULT_K = 5

# Labels/relationship types cannot be parameterized in Cypher, so any tool
# that takes one as a model-supplied argument has to interpolate it into
# the query text. Validating it as a plain identifier first (no backticks,
# no Cypher syntax) closes the escape-the-backtick-quoting injection path
# before the string ever reaches the driver — on top of, not instead of,
# the read-only session and `cypher_guard`'s own checks.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InvalidIdentifierError(ValueError):
    pass


def _validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.match(value):
        raise InvalidIdentifierError(f"Not a valid label/relationship-type identifier: {value!r}")
    return value


# Raw JSON-blob properties that exist so the graph keeps a compact source
# summary on parent nodes (see docs/GRAPH_DATA_HANDOFF.md), not because a
# model answering a question needs them. Dominates token cost for little
# benefit; `get_full_node` is the escape hatch when they genuinely matter.
_RAW_BLOB_PROPERTIES = {
    "versions_raw", "comments_raw", "code_changes_raw", "reviews_raw",
    "participants_raw", "recipients_raw",
}


def strip_raw_blob_properties(obj):
    """Recursively drops raw JSON-blob properties from any tool result.

    Applied once, centrally, in `nodes.run_tool_loop` to every tool's
    return value — the single shared projection point
    (`FINAL_REMEDIATION_ORDER_GRAPH_RAG_AGENT.md` item 2), not scattered as
    a per-tool concern. Every tool that returns node data is covered by
    construction, including ones added later; nothing has to remember to
    call this. `get_full_node` is exempted by the caller, not by this
    function, since it is specifically the untrimmed escape hatch.
    """
    if isinstance(obj, dict):
        return {
            key: strip_raw_blob_properties(value)
            for key, value in obj.items()
            if key not in _RAW_BLOB_PROPERTIES
        }
    if isinstance(obj, list):
        return [strip_raw_blob_properties(item) for item in obj]
    return obj


# What each label holds, in the model's own words, so tool selection does
# not require guessing. Order matches embedding_pass.LABELS.
_LABEL_DESCRIPTIONS = {
    "MailMessage": (
        "Email messages between team members and external contacts, including "
        "customer reports. Use for questions about what was communicated to "
        "customers or stakeholders by email."
    ),
    "SlackMessage": (
        "Slack channel messages and thread replies between team members. Use "
        "for informal team discussion, quick decisions, and day-to-day "
        "coordination."
    ),
    "TeamsTranscriptSegment": (
        "Individual spoken lines from Teams meeting transcripts. Use for "
        "questions about what was said or decided in a specific meeting, "
        "or who said what."
    ),
    "IssueVersion": (
        "Successive versions of issue titles, descriptions, and acceptance "
        "criteria. Use for questions about requirements, scope, or how a "
        "ticket's definition changed over time. Every version is indexed, "
        "not just the current one."
    ),
    "IssueComment": (
        "Comments posted on issues/tickets. Use for discussion and decisions "
        "recorded directly on a ticket, separate from its formal description."
    ),
    "DocumentVersion": (
        "Successive versions of requirements and technical documents "
        "(requirement specs, technical designs, decision records, runbooks). "
        "Use for the authoritative written record of a requirement or design "
        "decision, and for how it changed over time."
    ),
    "PullRequestReview": (
        "Pull request review comments and review decisions (approved, "
        "changes requested, inline code comments). Use for questions about "
        "code review feedback or why a PR was or was not approved."
    ),
    "PullRequest": (
        "Pull request titles and descriptions. Use for what a PR set out to "
        "do, as opposed to what reviewers said about it."
    ),
    "Topic": (
        "AI-derived storylines grouping one or more issues into a single "
        "narrative, with a summary. Use as a first entry point for broad "
        "'what is the story of X' questions."
    ),
    "Event": (
        "AI-derived individual events in the project timeline, with "
        "summaries and event types (created, decided, blocked, changed, "
        "resolved, regressed). Use as a first entry point for 'what "
        "happened' and 'why' questions — this is usually the best starting "
        "point for causal questions, since events are linked to each other "
        "by CAUSED edges."
    ),
}

_LABEL_KEY_PROPERTIES = {
    "MailMessage": ["message_id"],
    "SlackMessage": ["message_id"],
    "TeamsTranscriptSegment": ["segment_id"],
    "IssueVersion": ["display_name", "issue_id", "version_number"],
    "IssueComment": ["comment_id"],
    "DocumentVersion": ["display_name", "document_id", "version_number"],
    "PullRequestReview": ["display_name", "source_id"],
    "PullRequest": ["display_name", "repository", "pr_number"],
    "Topic": ["slug"],
    "Event": ["topic_slug", "slug"],
}


def _clamp_k(k) -> int:
    try:
        k = int(k)
    except (TypeError, ValueError):
        return DEFAULT_K
    return max(1, min(MAX_K, k))


def _node_key(label: str, props: dict) -> str:
    return props.get("display_name") or props.get("name") or props.get("slug") or props.get("issue_key") or ""


def _text_preview(props: dict, limit: int = 280) -> str:
    for field in ("summary", "body", "description", "title"):
        value = props.get(field)
        if value:
            return value[:limit]
    return ""


def _embed_query(client, text: str) -> list[float]:
    response = client.embeddings.create(model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS, input=[text])
    return response.data[0].embedding


def _vector_search(session, index_name: str, label: str, query_vector: list[float], k: int) -> list[dict]:
    def _read(tx):
        return [dict(row) for row in tx.run(
            """
            CALL db.index.vector.queryNodes($index_name, $k, $query_vector)
            YIELD node, score
            RETURN properties(node) AS props, score
            """,
            {"index_name": index_name, "k": k, "query_vector": query_vector},
        )]

    rows = session.execute_read(_read)
    results = []
    for row in rows:
        props = row["props"]
        results.append({
            "label": label,
            "key": _node_key(label, props),
            "display_name": props.get("display_name") or props.get("name"),
            "score": row["score"],
            "text_preview": _text_preview(props),
            "source_url": props.get("source_url"),
        })
    return results


# ---------------------------------------------------------------------------
# Per-label vector search tools.
# ---------------------------------------------------------------------------

def build_vector_search_schemas() -> list[dict]:
    schemas = []
    for cfg in EMBEDDING_LABELS:
        schemas.append({
            "type": "function",
            "name": f"search_{cfg.label.lower()}",
            "description": _LABEL_DESCRIPTIONS[cfg.label],
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search text, in natural language."},
                    "k": {"type": "integer", "description": f"Number of results, default {DEFAULT_K}, max {MAX_K}."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": False,
        })
    return schemas


VECTOR_SEARCH_SCHEMAS = build_vector_search_schemas()


def build_vector_search_executors(session, client) -> dict[str, Callable]:
    executors = {}
    for cfg in EMBEDDING_LABELS:
        def make_executor(label=cfg.label, index_name=cfg.index_name):
            def executor(args: dict):
                query_vector = _embed_query(client, args["query"])
                return _vector_search(session, index_name, label, query_vector, _clamp_k(args.get("k")))
            return executor
        executors[f"search_{cfg.label.lower()}"] = make_executor()
    return executors


# ---------------------------------------------------------------------------
# Broad multi-index search.
# ---------------------------------------------------------------------------

SEARCH_ALL_SCHEMA = {
    "type": "function",
    "name": "search_all",
    "description": (
        "Runs the query against every vector index and returns a merged, "
        "rank-normalized list of hits with their label. Use when you cannot "
        "tell which label the answer lives in, or the question plausibly "
        "spans several source types."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search text, in natural language."},
            "k": {"type": "integer", "description": f"Number of merged results to return, default {DEFAULT_K}, max {MAX_K}."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "strict": False,
}


def _search_all(session, client, query: str, k: int) -> list[dict]:
    query_vector = _embed_query(client, query)
    per_index_k = max(k, DEFAULT_K)

    merged: list[dict] = []
    for cfg in EMBEDDING_LABELS:
        hits = _vector_search(session, cfg.index_name, cfg.label, query_vector, per_index_k)
        # Rank-based normalization: raw cosine scores are not comparable
        # across indexes trained on different text distributions, but rank
        # within one index is a fair signal. Reciprocal rank keeps the top
        # hit from each index competitive with top hits from every other.
        for rank, hit in enumerate(hits):
            hit["rank_score"] = 1.0 / (rank + 1)
            merged.append(hit)

    merged.sort(key=lambda hit: hit["rank_score"], reverse=True)
    return merged[:k]


def build_search_all_executor(session, client) -> Callable:
    def executor(args: dict):
        return _search_all(session, client, args["query"], _clamp_k(args.get("k")))
    return executor


# ---------------------------------------------------------------------------
# Fulltext lookups.
# ---------------------------------------------------------------------------

LOOKUP_ENTITY_SCHEMA = {
    "type": "function",
    "name": "lookup_entity",
    "description": (
        "Fulltext search over Person, Issue, Document, PullRequest, and "
        "Topic names, display names, and titles. Use to resolve a name or "
        "title you need to match, not for semantic content search."
    ),
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The name, title, or phrase to look up."}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "strict": False,
}

LOOKUP_ISSUE_KEY_SCHEMA = {
    "type": "function",
    "name": "lookup_issue_key",
    "description": "Exact/fuzzy fulltext lookup of an issue key such as AUTH-17.",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The issue key to look up, e.g. AUTH-17."}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "strict": False,
}


def _lookup_entity(session, text: str) -> list[dict]:
    def _read(tx):
        return [dict(row) for row in tx.run(
            """
            CALL db.index.fulltext.queryNodes('entity_lookup', $text) YIELD node, score
            RETURN labels(node)[0] AS label, properties(node) AS props, score
            LIMIT 10
            """,
            {"text": text},
        )]

    rows = session.execute_read(_read)
    return [
        {
            "label": row["label"],
            "key": _node_key(row["label"], row["props"]),
            "display_name": row["props"].get("display_name") or row["props"].get("name"),
            "score": row["score"],
        }
        for row in rows
    ]


def _lookup_issue_key(session, text: str) -> list[dict]:
    def _read(tx):
        return [dict(row) for row in tx.run(
            """
            CALL db.index.fulltext.queryNodes('issue_key_lookup', $text) YIELD node, score
            RETURN properties(node) AS props, score
            LIMIT 10
            """,
            {"text": text},
        )]

    rows = session.execute_read(_read)
    return [
        {
            "label": "Issue",
            "key": row["props"].get("issue_key"),
            "display_name": row["props"].get("display_name") or row["props"].get("issue_key"),
            "score": row["score"],
        }
        for row in rows
    ]


def build_fulltext_executors(session) -> dict[str, Callable]:
    return {
        "lookup_entity": lambda args: _lookup_entity(session, args["text"]),
        "lookup_issue_key": lambda args: _lookup_issue_key(session, args["text"]),
    }


# ---------------------------------------------------------------------------
# Fixed traversal tools.
# ---------------------------------------------------------------------------

GET_NODE_CONTEXT_SCHEMA = {
    "type": "function",
    "name": "get_node_context",
    "description": "The node identified by label and key, plus its immediate neighbours with relationship types and directions.",
    "parameters": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "The node's Neo4j label, e.g. Issue, Person, PullRequest."},
            "key": {"type": "string", "description": "The node's natural key (display_name, name, person_key, slug, or issue_key)."},
        },
        "required": ["label", "key"],
        "additionalProperties": False,
    },
    "strict": False,
}

GET_EVENT_EVIDENCE_SCHEMA = {
    "type": "function",
    "name": "get_event_evidence",
    "description": "An AI-derived event: its evidence (EVIDENCED_BY targets with text), its actors (ACTED_IN_EVENT), and its inbound/outbound CAUSED links.",
    "parameters": {
        "type": "object",
        "properties": {
            "topic_slug": {"type": "string"},
            "event_slug": {"type": "string"},
        },
        "required": ["topic_slug", "event_slug"],
        "additionalProperties": False,
    },
    "strict": False,
}

GET_PERSON_ACTIVITY_SCHEMA = {
    "type": "function",
    "name": "get_person_activity",
    "description": "Everything one person authored, sent, reviewed, commented on, or participated in, across all six source groups.",
    "parameters": {
        "type": "object",
        "properties": {"person_key": {"type": "string", "description": "The person's person_key, e.g. email:anna.berg@example.com."}},
        "required": ["person_key"],
        "additionalProperties": False,
    },
    "strict": False,
}

GET_ISSUE_HISTORY_SCHEMA = {
    "type": "function",
    "name": "get_issue_history",
    "description": "An issue's identity, every IssueVersion in order with who changed it, and every IssueComment with its author.",
    "parameters": {
        "type": "object",
        "properties": {"issue_key": {"type": "string", "description": "The issue key, e.g. AUTH-17."}},
        "required": ["issue_key"],
        "additionalProperties": False,
    },
    "strict": False,
}


def _get_node_context(session, label: str, key: str):
    _validate_identifier(label)

    def _read(tx):
        node_row = tx.run(
            f"""
            MATCH (n:`{label}`)
            WHERE n.display_name = $key OR n.name = $key OR n.person_key = $key
               OR n.slug = $key OR n.issue_key = $key
            RETURN properties(n) AS props
            LIMIT 1
            """,
            {"key": key},
        ).single()
        if not node_row:
            return None

        neighbours = tx.run(
            f"""
            MATCH (n:`{label}`)
            WHERE n.display_name = $key OR n.name = $key OR n.person_key = $key
               OR n.slug = $key OR n.issue_key = $key
            MATCH (n)-[r]-(m)
            RETURN type(r) AS relationship_type,
                   CASE WHEN startNode(r) = n THEN 'outgoing' ELSE 'incoming' END AS direction,
                   labels(m)[0] AS neighbour_label,
                   coalesce(m.display_name, m.name, m.person_key, m.slug, m.issue_key) AS neighbour_key
            LIMIT 200
            """,
            {"key": key},
        ).data()
        return {"node": node_row["props"], "neighbours": neighbours}

    return session.execute_read(_read)


def _get_full_node(session, label: str, key: str):
    _validate_identifier(label)

    def _read(tx):
        row = tx.run(
            f"""
            MATCH (n:`{label}`)
            WHERE n.display_name = $key OR n.name = $key OR n.person_key = $key
               OR n.slug = $key OR n.issue_key = $key
            RETURN properties(n) AS props
            LIMIT 1
            """,
            {"key": key},
        ).single()
        return row["props"] if row else None

    return session.execute_read(_read)


def _get_event_evidence(session, topic_slug: str, event_slug: str):
    def _read(tx):
        row = tx.run(
            """
            MATCH (e:Event {topic_slug: $topic_slug, slug: $event_slug})
            OPTIONAL MATCH (e)-[:EVIDENCED_BY]->(ev)
            OPTIONAL MATCH (p:Person)-[:ACTED_IN_EVENT]->(e)
            OPTIONAL MATCH (e)-[out:CAUSED]->(effect:Event)
            OPTIONAL MATCH (cause:Event)-[incoming:CAUSED]->(e)
            RETURN properties(e) AS event,
                   collect(DISTINCT {
                     label: labels(ev)[0],
                     key: coalesce(ev.display_name, ev.name),
                     text: coalesce(ev.body, ev.description, ev.summary)
                   }) AS evidence,
                   collect(DISTINCT p.name) AS actors,
                   collect(DISTINCT {effect_slug: effect.slug, explanation: out.explanation}) AS caused,
                   collect(DISTINCT {cause_slug: cause.slug, explanation: incoming.explanation}) AS caused_by
            """,
            {"topic_slug": topic_slug, "event_slug": event_slug},
        ).single()
        return dict(row) if row else None

    return session.execute_read(_read)


def _get_person_activity(session, person_key: str):
    def _read(tx):
        row = tx.run(
            """
            MATCH (p:Person {person_key: $person_key})
            OPTIONAL MATCH (p)-[:SENT_MAIL]->(m:MailMessage)
            OPTIONAL MATCH (p)-[:SENT_SLACK_MESSAGE]->(s:SlackMessage)
            OPTIONAL MATCH (p)-[:PARTICIPATED_IN_MEETING]->(tm:TeamsMeeting)
            OPTIONAL MATCH (p)-[:CREATED_ISSUE]->(created:Issue)
            OPTIONAL MATCH (p)-[:OWNS_ISSUE]->(owned:Issue)
            OPTIONAL MATCH (p)-[:WROTE_ISSUE_COMMENT]->(ic:IssueComment)
            OPTIONAL MATCH (p)-[:AUTHORED_DOCUMENT]->(d:Document)
            OPTIONAL MATCH (p)-[:AUTHORED_PR]->(authored_pr:PullRequest)
            OPTIONAL MATCH (p)-[:REVIEWED_PR]->(reviewed_pr:PullRequest)
            OPTIONAL MATCH (p)-[:WROTE_PR_REVIEW]->(rev:PullRequestReview)
            OPTIONAL MATCH (p)-[:ACTED_IN_EVENT]->(e:Event)
            RETURN properties(p) AS person,
                   collect(DISTINCT m.display_name) AS mail_sent,
                   collect(DISTINCT s.display_name) AS slack_sent,
                   collect(DISTINCT tm.title) AS meetings,
                   collect(DISTINCT created.issue_key) AS issues_created,
                   collect(DISTINCT owned.issue_key) AS issues_owned,
                   collect(DISTINCT ic.comment_id) AS issue_comments,
                   collect(DISTINCT d.title) AS documents_authored,
                   collect(DISTINCT authored_pr.display_name) AS prs_authored,
                   collect(DISTINCT reviewed_pr.display_name) AS prs_reviewed,
                   collect(DISTINCT rev.display_name) AS pr_reviews,
                   collect(DISTINCT e.name) AS events_acted_in
            """,
            {"person_key": person_key},
        ).single()
        return dict(row) if row else None

    return session.execute_read(_read)


def _get_issue_history(session, issue_key: str):
    def _read(tx):
        issue_row = tx.run(
            "MATCH (i:Issue {issue_key: $key}) RETURN properties(i) AS props",
            {"key": issue_key},
        ).single()
        if not issue_row:
            return None

        versions = tx.run(
            """
            MATCH (i:Issue {issue_key: $key})-[:HAS_ISSUE_VERSION]->(v:IssueVersion)
            OPTIONAL MATCH (p:Person)-[:CHANGED_ISSUE_VERSION]->(v)
            RETURN properties(v) AS version, collect(DISTINCT p.name) AS changed_by
            ORDER BY v.version_number
            """,
            {"key": issue_key},
        ).data()

        comments = tx.run(
            """
            MATCH (i:Issue {issue_key: $key})-[:HAS_ISSUE_COMMENT]->(c:IssueComment)
            OPTIONAL MATCH (p:Person)-[:WROTE_ISSUE_COMMENT]->(c)
            RETURN properties(c) AS comment, collect(DISTINCT p.name) AS author
            ORDER BY c.created_at
            """,
            {"key": issue_key},
        ).data()

        return {"issue": issue_row["props"], "versions": versions, "comments": comments}

    return session.execute_read(_read)


# ---------------------------------------------------------------------------
# Aggregate / counting tools — fixed Cypher, never generated. Structural
# questions about the graph as a whole should never depend on the model
# writing an aggregate query by hand, which is both the expensive path and
# the one most likely to be subtly wrong.
# ---------------------------------------------------------------------------

COUNT_NODES_SCHEMA = {
    "type": "function",
    "name": "count_nodes",
    "description": (
        "Counts nodes. With no label, returns the total and a per-label "
        "breakdown for the whole graph in one call. With a label, returns "
        "just that count. Prefer this over run_cypher for any 'how many' "
        "question about nodes."
    ),
    "parameters": {
        "type": "object",
        "properties": {"label": {"type": "string", "description": "Optional: a single Neo4j label to count."}},
        "required": [],
        "additionalProperties": False,
    },
    "strict": False,
}

COUNT_RELATIONSHIPS_SCHEMA = {
    "type": "function",
    "name": "count_relationships",
    "description": (
        "Counts relationships. With no type, returns the total and a "
        "per-type breakdown for the whole graph in one call. With a type, "
        "returns just that count. Prefer this over run_cypher for any "
        "'how many' question about relationships."
    ),
    "parameters": {
        "type": "object",
        "properties": {"type": {"type": "string", "description": "Optional: a single relationship type to count."}},
        "required": [],
        "additionalProperties": False,
    },
    "strict": False,
}

DESCRIBE_GRAPH_SCHEMA = {
    "type": "function",
    "name": "describe_graph",
    "description": (
        "The full label and relationship-type inventory with counts, in "
        "one call. Use as a first step for broad 'what does the graph "
        "contain' or 'how big is the graph' questions."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    "strict": False,
}

GET_FULL_NODE_SCHEMA = {
    "type": "function",
    "name": "get_full_node",
    "description": (
        "The complete, untrimmed properties of one node by label and key, "
        "including raw JSON blob properties (versions_raw, "
        "code_changes_raw, comments_raw, reviews_raw, participants_raw, "
        "recipients_raw) that get_node_context omits. Use only when those "
        "blobs themselves are what the question needs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "The node's Neo4j label."},
            "key": {"type": "string", "description": "The node's natural key (display_name, name, person_key, slug, or issue_key)."},
        },
        "required": ["label", "key"],
        "additionalProperties": False,
    },
    "strict": False,
}


def _count_nodes(session, label: str | None = None):
    if label:
        _validate_identifier(label)

        def _read_one(tx):
            return tx.run(f"MATCH (n:`{label}`) RETURN count(n) AS count").single()["count"]

        return {"label": label, "count": session.execute_read(_read_one)}

    def _read_all(tx):
        per_label = [dict(row) for row in tx.run("""
            CALL db.labels() YIELD label
            CALL (label) {
                WITH label
                MATCH (n) WHERE label IN labels(n)
                RETURN count(n) AS count
            }
            RETURN label, count
            ORDER BY label
        """)]
        total = tx.run("MATCH (n) RETURN count(n) AS total").single()["total"]
        return per_label, total

    per_label, total = session.execute_read(_read_all)
    return {"per_label": per_label, "total_nodes": total}


def _count_relationships(session, rel_type: str | None = None):
    if rel_type:
        _validate_identifier(rel_type)

        def _read_one(tx):
            return tx.run(f"MATCH ()-[r:`{rel_type}`]->() RETURN count(r) AS count").single()["count"]

        return {"type": rel_type, "count": session.execute_read(_read_one)}

    def _read_all(tx):
        per_type = [dict(row) for row in tx.run("""
            CALL db.relationshipTypes() YIELD relationshipType
            CALL (relationshipType) {
                WITH relationshipType
                MATCH ()-[r]->() WHERE type(r) = relationshipType
                RETURN count(r) AS count
            }
            RETURN relationshipType AS type, count
            ORDER BY type
        """)]
        total = tx.run("MATCH ()-[r]->() RETURN count(r) AS total").single()["total"]
        return per_type, total

    per_type, total = session.execute_read(_read_all)
    return {"per_type": per_type, "total_relationships": total}


def _describe_graph(session):
    return {"nodes": _count_nodes(session), "relationships": _count_relationships(session)}


def build_fixed_traversal_executors(session) -> dict[str, Callable]:
    return {
        "get_node_context": lambda args: _get_node_context(session, args["label"], args["key"]),
        "get_full_node": lambda args: _get_full_node(session, args["label"], args["key"]),
        "get_event_evidence": lambda args: _get_event_evidence(session, args["topic_slug"], args["event_slug"]),
        "get_person_activity": lambda args: _get_person_activity(session, args["person_key"]),
        "get_issue_history": lambda args: _get_issue_history(session, args["issue_key"]),
        "count_nodes": lambda args: _count_nodes(session, args.get("label")),
        "count_relationships": lambda args: _count_relationships(session, args.get("type")),
        "describe_graph": lambda args: _describe_graph(session),
    }


# ---------------------------------------------------------------------------
# Free Cypher, read-only enforced.
# ---------------------------------------------------------------------------

RUN_CYPHER_SCHEMA = {
    "type": "function",
    "name": "run_cypher",
    "description": (
        "Runs a read-only Cypher query against the live graph. Write "
        "operations are rejected before execution. A LIMIT is injected "
        "automatically if you omit one. Use this for anything the four "
        "fixed traversal tools do not cover. Write queries against the "
        "schema you were given in your system prompt, not from memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A read-only Cypher query."},
            "params": {"type": "object", "description": "Optional query parameters.", "additionalProperties": True},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "strict": False,
}


def _run_cypher(session, query: str, params: dict | None):
    try:
        guarded = enforce_read_only(query)
    except CypherWriteRejected as error:
        return {"rejected": True, "error": str(error)}

    # `Query(..., timeout=...)` is only accepted by `Session.run`, not by a
    # managed-transaction `tx.run` (confirmed live: "Query object is only
    # supported for session.run"). The timeout has to be set when the
    # transaction itself is opened instead.
    try:
        with session.begin_transaction(timeout=QUERY_TIMEOUT_SECONDS) as tx:
            result = tx.run(guarded, params or {})
            rows = [dict(row) for row in result]
        return {"rejected": False, "rows": rows}
    except Exception as error:  # noqa: BLE001 - surfaced to the model as a tool error, not raised
        return {"rejected": False, "error": str(error)}


def build_run_cypher_executor(session) -> Callable:
    return lambda args: _run_cypher(session, args["query"], args.get("params"))


# ---------------------------------------------------------------------------
# Assembly for each specialist.
# ---------------------------------------------------------------------------

def search_agent_tools(session, client) -> tuple[list[dict], dict[str, Callable]]:
    schemas = [*VECTOR_SEARCH_SCHEMAS, SEARCH_ALL_SCHEMA, LOOKUP_ENTITY_SCHEMA, LOOKUP_ISSUE_KEY_SCHEMA]
    executors = {
        **build_vector_search_executors(session, client),
        "search_all": build_search_all_executor(session, client),
        **build_fulltext_executors(session),
    }
    return schemas, executors


def graph_agent_tools(session) -> tuple[list[dict], dict[str, Callable]]:
    schemas = [
        GET_NODE_CONTEXT_SCHEMA, GET_FULL_NODE_SCHEMA, GET_EVENT_EVIDENCE_SCHEMA,
        GET_PERSON_ACTIVITY_SCHEMA, GET_ISSUE_HISTORY_SCHEMA,
        COUNT_NODES_SCHEMA, COUNT_RELATIONSHIPS_SCHEMA, DESCRIBE_GRAPH_SCHEMA,
        RUN_CYPHER_SCHEMA,
    ]
    executors = {
        **build_fixed_traversal_executors(session),
        "run_cypher": build_run_cypher_executor(session),
    }
    return schemas, executors
