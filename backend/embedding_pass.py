"""On-demand embedding pass over the Neo4j graph.

Ten source and derived labels get a `embedding` vector property, computed
from a per-label text assembly and written through
`db.create.setNodeVectorProperty`. The pass is incremental: each node also
carries `embedding_model` and `embedding_source_hash`, and a node is only
re-embedded when its assembled text or the configured model has changed
since the last run. `force=True` bypasses that check and re-embeds
everything.

This never touches PostgreSQL and never calls the knowledge-layer or
reference-extraction passes. It reads whatever `IssueVersion`, `Document
Version`, `Topic`, `Event`, etc. currently look like in Neo4j and embeds
that — it does not care whether those nodes came from an import or from a
knowledge-layer build.

See `WORK_ORDER_EMBEDDINGS.md` for the decisions behind every choice below,
and `VECTOR_INDEX_INFO_RESPONSE.md` / `KNOWLEDGE_LAYER_INFO_RESPONSE.md` for
the measurements those decisions rest on.
"""

import hashlib
import re
from typing import Callable, Optional

from reference_extraction import now_iso, touch_pipeline_state
from topic_event_extraction import MissingApiKeyError, require_openai_client
from pipeline_staleness import compute_staleness
from pipeline_staleness import read_pipeline_state as _read_full_pipeline_state

EMBEDDING_VERSION = "embedding-v1"
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1536
EMBEDDING_SIMILARITY = "cosine"
BATCH_SIZE = 100

_HEADING_PATTERN = re.compile(r"(?m)^#{1,6}[ \t]+")


def _strip_markdown_headings(text: str) -> str:
    """`## Background` -> `Background`. Nothing else is touched."""
    return _HEADING_PATTERN.sub("", text)


def _build_text(parts: list[tuple[Optional[str], Optional[str]]]) -> str:
    """Join non-empty labelled/unlabelled parts with blank lines.

    A part is (label, value). label=None means no "Label: " prefix. A part
    whose value is null or blank after stripping is omitted entirely rather
    than emitted as an empty labelled line.
    """
    pieces = []
    for label, value in parts:
        if not value:
            continue
        value = value.strip()
        if not value:
            continue
        pieces.append(f"{label}: {value}" if label else value)
    return "\n\n".join(pieces)


def _mail_message_text(props):
    return _build_text([("Subject", props.get("subject")), (None, props.get("body"))])


def _slack_message_text(props):
    return _build_text([(None, props.get("body"))])


def _teams_transcript_segment_text(props):
    return _build_text([(None, props.get("body"))])


def _issue_version_text(props):
    return _build_text([
        ("Title", props.get("title")),
        ("Description", props.get("description")),
        ("Acceptance criteria", props.get("acceptance_criteria")),
    ])


def _issue_comment_text(props):
    return _build_text([(None, props.get("body"))])


def _document_version_text(props):
    body = props.get("body")
    if body:
        body = _strip_markdown_headings(body)
    return _build_text([("Title", props.get("title")), (None, body)])


def _pull_request_review_text(props):
    return _build_text([(None, props.get("body"))])


def _pull_request_text(props):
    return _build_text([("Title", props.get("title")), ("Description", props.get("description"))])


def _topic_text(props):
    return _build_text([(None, props.get("name")), (None, props.get("summary"))])


def _event_text(props):
    return _build_text([(None, props.get("name")), (None, props.get("summary"))])


def _component_text(props):
    return _build_text([(None, props.get("name")), (None, props.get("component_type")), (None, props.get("summary"))])


def _root_cause_text(props):
    return _build_text([(None, props.get("name")), (None, props.get("cause_type")), (None, props.get("summary"))])


class LabelConfig:
    def __init__(self, label: str, key_props: list[str], text_fn: Callable[[dict], str]):
        self.label = label
        self.key_props = key_props
        self.text_fn = text_fn
        self.index_name = f"{label.lower()}_embedding"


# Key props match each label's existing Neo4j uniqueness constraint
# (see GRAPH_SCHEMA_HANDOFF.md section 2), so the write MATCH is an
# indexed lookup rather than a label scan.
LABELS = [
    LabelConfig("MailMessage", ["source_instance", "message_id"], _mail_message_text),
    LabelConfig(
        "SlackMessage",
        ["source_instance", "workspace_id", "channel_id", "message_id", "version_number"],
        _slack_message_text,
    ),
    LabelConfig(
        "TeamsTranscriptSegment",
        ["source_instance", "meeting_id", "segment_id"],
        _teams_transcript_segment_text,
    ),
    LabelConfig(
        "IssueVersion",
        ["source_instance", "issue_id", "version_number"],
        _issue_version_text,
    ),
    LabelConfig("IssueComment", ["source_instance", "comment_id"], _issue_comment_text),
    LabelConfig(
        "DocumentVersion",
        ["source_instance", "document_id", "version_number"],
        _document_version_text,
    ),
    LabelConfig(
        "PullRequestReview",
        ["source_instance", "repository", "pr_number", "source_id"],
        _pull_request_review_text,
    ),
    LabelConfig("PullRequest", ["source_instance", "repository", "pr_number"], _pull_request_text),
    LabelConfig("Topic", ["slug"], _topic_text),
    LabelConfig("Event", ["topic_slug", "slug"], _event_text),
    LabelConfig("Component", ["source_instance", "repository", "slug"], _component_text),
    LabelConfig("RootCause", ["slug"], _root_cause_text),
]

FULLTEXT_ENTITY_LABELS = ["Person", "Issue", "Document", "PullRequest", "Topic"]
FULLTEXT_ENTITY_PROPERTIES = ["name", "display_name", "title"]


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# Index setup. Idempotent, safe to run at the start of every pass.
# ---------------------------------------------------------------------------

def ensure_vector_indexes(tx):
    for cfg in LABELS:
        tx.run(f"""
            CREATE VECTOR INDEX {cfg.index_name} IF NOT EXISTS
            FOR (n:`{cfg.label}`) ON (n.embedding)
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: $dimensions,
              `vector.similarity_function`: $similarity
            }}}}
        """, {"dimensions": EMBEDDING_DIMENSIONS, "similarity": EMBEDDING_SIMILARITY})


def ensure_fulltext_indexes(tx):
    label_pattern = "|".join(FULLTEXT_ENTITY_LABELS)
    properties = ", ".join(f"n.{prop}" for prop in FULLTEXT_ENTITY_PROPERTIES)
    tx.run(f"""
        CREATE FULLTEXT INDEX entity_lookup IF NOT EXISTS
        FOR (n:{label_pattern})
        ON EACH [{properties}]
    """)
    tx.run("""
        CREATE FULLTEXT INDEX issue_key_lookup IF NOT EXISTS
        FOR (n:Issue) ON EACH [n.issue_key]
    """)


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------

def read_label_nodes(tx, label):
    return [dict(row) for row in tx.run(f"""
        MATCH (n:`{label}`)
        RETURN properties(n) AS props
    """)]


def read_label_state(tx, label):
    row = tx.run(f"""
        MATCH (n:`{label}`)
        RETURN count(n) AS total,
               count(n.embedding) AS embedded
    """).single()
    return {"label": label, "total": row["total"], "embedded": row["embedded"],
            "missing": row["total"] - row["embedded"]}


def read_embedding_models_in_use(tx):
    rows = tx.run("""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN $labels) AND n.embedding_model IS NOT NULL
        RETURN DISTINCT n.embedding_model AS model
    """, {"labels": [cfg.label for cfg in LABELS]})
    return sorted(row["model"] for row in rows)


def read_pipeline_state_for_embeddings(tx):
    # Reads every stage's timestamp (not just this layer's own upstream) so
    # that staleness can be computed transitively. See `pipeline_staleness.py`.
    return _read_full_pipeline_state(tx)


def load_embedding_state(session):
    def _read(tx):
        per_label = [read_label_state(tx, cfg.label) for cfg in LABELS]
        models_in_use = read_embedding_models_in_use(tx)
        pipeline = read_pipeline_state_for_embeddings(tx)
        return {"per_label": per_label, "models_in_use": models_in_use, "pipeline": pipeline}

    result = session.execute_read(_read)
    pipeline = result["pipeline"]
    staleness = compute_staleness(pipeline)["embeddings"]
    return {
        "per_label": result["per_label"],
        "models_in_use": result["models_in_use"],
        "configured_model": EMBEDDING_MODEL,
        "configured_dimensions": EMBEDDING_DIMENSIONS,
        "last_embedding_at": pipeline["last_embedding_at"],
        "last_import_at": pipeline["last_import_at"],
        "last_layer_build_at": pipeline["last_layer_build_at"],
        "last_architecture_build_at": pipeline["last_architecture_build_at"],
        "last_causal_build_at": pipeline["last_causal_build_at"],
        "needs_rerun": staleness["stale"],
        "stale_reasons": staleness["reasons"],
    }


def embedding_state_payload(session):
    return load_embedding_state(session)


# ---------------------------------------------------------------------------
# Writes.
# ---------------------------------------------------------------------------

def write_embedding(tx, label, key, vector, model, source_hash, embedded_at):
    key_pattern = ", ".join(f"{prop}: $key.{prop}" for prop in key)
    tx.run(f"""
        MATCH (n:`{label}` {{{key_pattern}}})
        CALL db.create.setNodeVectorProperty(n, 'embedding', $vector)
        SET n.embedding_model = $model,
            n.embedding_source_hash = $hash,
            n.embedded_at = $embedded_at
    """, {"key": key, "vector": vector, "model": model, "hash": source_hash, "embedded_at": embedded_at})


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def run_embedding_pass(session, force=False):
    client = require_openai_client()
    embedded_at = now_iso()

    session.execute_write(ensure_vector_indexes)
    session.execute_write(ensure_fulltext_indexes)

    per_label_report = []
    failures = []
    total_embedded = 0
    total_skipped_unchanged = 0
    total_skipped_empty = 0
    total_tokens = 0
    has_token_usage = False

    for cfg in LABELS:
        rows = session.execute_read(read_label_nodes, cfg.label)

        to_embed = []
        skipped_unchanged = 0
        skipped_empty = 0

        for row in rows:
            props = row["props"]
            text = cfg.text_fn(props)
            if not text:
                skipped_empty += 1
                continue

            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            unchanged = (
                not force
                and props.get("embedding_source_hash") == text_hash
                and props.get("embedding_model") == EMBEDDING_MODEL
            )
            if unchanged:
                skipped_unchanged += 1
                continue

            key = {prop: props.get(prop) for prop in cfg.key_props}
            to_embed.append({"key": key, "text": text, "hash": text_hash})

        embedded = 0
        label_failures = []

        for batch in _chunked(to_embed, BATCH_SIZE):
            try:
                response = client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    dimensions=EMBEDDING_DIMENSIONS,
                    input=[item["text"] for item in batch],
                )
            except Exception as error:
                for item in batch:
                    label_failures.append({"label": cfg.label, "key": item["key"], "error": str(error)})
                continue

            usage = getattr(response, "usage", None)
            if usage is not None:
                has_token_usage = True
                total_tokens += usage.total_tokens

            for item, embedding_row in zip(batch, response.data):
                try:
                    session.execute_write(
                        write_embedding, cfg.label, item["key"], embedding_row.embedding,
                        EMBEDDING_MODEL, item["hash"], embedded_at,
                    )
                    embedded += 1
                except Exception as error:
                    label_failures.append({"label": cfg.label, "key": item["key"], "error": str(error)})

        per_label_report.append({
            "label": cfg.label,
            "embedded": embedded,
            "skipped_unchanged": skipped_unchanged,
            "skipped_empty": skipped_empty,
            "failed": len(label_failures),
        })
        failures.extend(label_failures)
        total_embedded += embedded
        total_skipped_unchanged += skipped_unchanged
        total_skipped_empty += skipped_empty

    session.execute_write(touch_pipeline_state, "last_embedding_at", embedded_at)

    state = load_embedding_state(session)
    state.update({
        "run_at": embedded_at,
        "forced": force,
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "per_label_run": per_label_report,
        "embedded": total_embedded,
        "skipped": total_skipped_unchanged + total_skipped_empty,
        "skipped_unchanged": total_skipped_unchanged,
        "skipped_empty": total_skipped_empty,
        "failed": len(failures),
        "failures": failures,
        "token_usage": {"total_tokens": total_tokens} if has_token_usage else None,
    })
    return state


def build_embeddings(session, force=False):
    return run_embedding_pass(session, force=force)
