"""LLM-driven topic and event extraction over the Neo4j graph.

Task 05 (`reference_extraction.py`) connects nodes by matching identifier
strings. That pass is exact but blind to any sentence that does not name an
identifier, and the most important sentence in this dataset is exactly such a
sentence: Priya's rejection of the proposed fix in `seg-003` never mentions
`AUTH-17`. This module builds the interpreted layer on top of the mechanical
one: for each `Issue`, it assembles every connected piece of evidence into one
bundle, asks a model to propose `Topic` and `Event` nodes and the causal links
between them, and then runs every proposal through a deterministic resolution
layer before anything is written. The model proposes; this code disposes.

This pass reads Neo4j to build bundles, calls OpenAI once per topic candidate,
resolves the person names it returns through `person_identity.build_registry`
(read-only against PostgreSQL, the same registry the SQL-to-Neo4j import
uses), and writes Neo4j. It never modifies PostgreSQL, and it never touches
the Task 05 reference edges, which are identified by `extracted_by` rather
than `generated_by`.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

from reference_extraction import now_iso, touch_pipeline_state
from pipeline_staleness import compute_staleness
from pipeline_staleness import read_pipeline_state as _read_full_pipeline_state

# `person_identity.py` lives in `viewer/`, not `backend/`. It is imported
# rather than duplicated because Task 06 must call `resolve_person_key`, not
# reimplement it.
_VIEWER_DIR = Path(__file__).resolve().parents[1] / "viewer"
if str(_VIEWER_DIR) not in sys.path:
    sys.path.insert(0, str(_VIEWER_DIR))

from person_identity import build_registry  # noqa: E402


OPENAI_MODEL = "gpt-5.6-terra"
OPENAI_REASONING_EFFORT = "medium"
EXTRACTION_VERSION = "topic-event-extraction-v1"

REFERENCE_EXTRACTOR_NAME = "reference-extraction-v1"
MAX_EVENTS_PER_TOPIC = 15
MAX_CAUSAL_LINKS_PER_TOPIC = 15

KNOWLEDGE_RELATIONSHIP_TYPES = [
    "ABOUT_TOPIC",
    "DERIVED_FROM",
    "EVENT_OF_TOPIC",
    "EVIDENCED_BY",
    "CAUSED",
    "ACTED_IN_EVENT",
]


# ---------------------------------------------------------------------------
# What the model returns. Structured outputs only: no free text, no JSON
# parsed out of a string.
# ---------------------------------------------------------------------------

class EventOut(BaseModel):
    slug: str
    name: str
    event_type: Literal["created", "decided", "blocked", "changed", "resolved", "regressed", "other"]
    occurred_at: str
    summary: str
    evidence: list[str]
    actor_names: list[str] = Field(default_factory=list)


class CausalLinkOut(BaseModel):
    cause_event_slug: str
    effect_event_slug: str
    explanation: str
    evidence: list[str]


class TopicOut(BaseModel):
    slug: str
    name: str
    topic_type: Literal["requirement", "defect", "incident", "decision", "other"]
    summary: str
    existing_topic_slug: Optional[str] = None


class TopicEventExtractionOut(BaseModel):
    topic: TopicOut
    events: list[EventOut]
    causal_links: list[CausalLinkOut]


EXTRACTION_INSTRUCTIONS = """\
You extract a causal story of Topics and Events from one issue's evidence bundle.

You will receive one issue and every piece of evidence connected to it: its \
versions, comments, and every source node reached through the deterministic \
reference layer, sorted chronologically. Every item carries a stable \
`identifier` that you must quote back exactly when you cite it as evidence. \
Never invent an identifier that is not in the bundle.

Rules:
- Every event must be grounded in the bundle. Do not report anything the \
texts do not support.
- `occurred_at` must come from a timestamp present in the evidence, never \
invented.
- Evidence identifiers must be copied exactly from the bundle.
- Prefer few well-evidenced events over many thin ones.
- A causal link requires something in the text indicating the connection, \
not merely that one thing preceded another.
- Before proposing a topic, check the list of topics that already exist. If \
this issue belongs to one of them, set `existing_topic_slug` to that exact \
slug and leave `slug`/`name` describing the same topic. Otherwise leave \
`existing_topic_slug` empty and propose a new topic.
"""


# ---------------------------------------------------------------------------
# Postgres connection. Duplicated in miniature from `viewer/app.py`'s
# `database_url()` rather than importing that module, which is a Flask app
# with its own routes and top-level side effects.
# ---------------------------------------------------------------------------

def postgres_database_url():
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "hm_data")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


class MissingApiKeyError(RuntimeError):
    pass


def require_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise MissingApiKeyError(
            "OPENAI_API_KEY is not configured. Add it to .env to build the knowledge layer."
        )
    return OpenAI(api_key=api_key)


# ---------------------------------------------------------------------------
# Bundle assembly (section 4).
# ---------------------------------------------------------------------------

def load_topic_candidates(tx):
    return [row["key"] for row in tx.run("""
        MATCH (i:Issue) WHERE i.issue_key IS NOT NULL
        RETURN i.issue_key AS key
        ORDER BY key
    """)]


def load_issue_basics(tx, issue_key):
    row = tx.run("""
        MATCH (i:Issue {issue_key: $issue_key})
        RETURN elementId(i) AS id, i.issue_key AS issue_key, i.title AS title,
               i.description AS description, i.status AS status,
               i.creator_name AS creator_name, i.created_at AS created_at
    """, {"issue_key": issue_key}).single()
    return dict(row) if row else None


def load_issue_versions(tx, issue_key):
    return [dict(row) for row in tx.run("""
        MATCH (:Issue {issue_key: $issue_key})-[:HAS_ISSUE_VERSION]->(v:IssueVersion)
        RETURN elementId(v) AS id, v.display_name AS display_name, v.version_at AS version_at,
               v.status AS status, v.changed_by_name AS changed_by_name, v.title AS title,
               v.description AS description, v.acceptance_criteria AS acceptance_criteria
        ORDER BY v.version_number
    """, {"issue_key": issue_key})]


def load_issue_comments(tx, issue_key):
    return [dict(row) for row in tx.run("""
        MATCH (:Issue {issue_key: $issue_key})-[:HAS_ISSUE_COMMENT]->(c:IssueComment)
        RETURN elementId(c) AS id, c.comment_id AS comment_id, c.author_name AS author_name,
               c.created_at AS created_at, c.version_at AS version_at, c.body AS body
    """, {"issue_key": issue_key})]


def load_reference_connected(tx, own_ids):
    """Every node reached from `own_ids` by a Task 05 reference edge, in either direction."""
    rows = tx.run("""
        UNWIND $own_ids AS oid
        MATCH (own) WHERE elementId(own) = oid
        OPTIONAL MATCH (own)-[r1:MENTIONS_ISSUE|MENTIONS_PULL_REQUEST|MENTIONS_DOCUMENT]->(outNode)
        WHERE r1.extracted_by = $extracted_by
        OPTIONAL MATCH (inNode)-[r2:MENTIONS_ISSUE|MENTIONS_PULL_REQUEST|MENTIONS_DOCUMENT]->(own)
        WHERE r2.extracted_by = $extracted_by
        WITH collect(DISTINCT outNode) + collect(DISTINCT inNode) AS nodes
        UNWIND nodes AS n
        WITH DISTINCT n WHERE n IS NOT NULL
        RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props
    """, {"own_ids": own_ids, "extracted_by": REFERENCE_EXTRACTOR_NAME})
    return [dict(row) for row in rows]


def load_pull_request_children(tx, pr_id):
    reviews = [dict(row) for row in tx.run("""
        MATCH (pr) WHERE elementId(pr) = $id
        MATCH (pr)-[:HAS_PR_REVIEW]->(review:PullRequestReview)
        RETURN elementId(review) AS id, review.source_id AS source_id,
               review.display_name AS display_name, review.author_name AS author_name,
               review.created_at AS created_at, review.version_at AS version_at,
               review.body AS body
    """, {"id": pr_id})]
    code_changes = [dict(row) for row in tx.run("""
        MATCH (pr) WHERE elementId(pr) = $id
        MATCH (pr)-[:HAS_CODE_CHANGE]->(change:CodeChange)
        RETURN elementId(change) AS id, change.display_name AS display_name,
               change.file_path AS file_path, change.change_type AS change_type,
               change.before_summary AS before_summary, change.after_summary AS after_summary
    """, {"id": pr_id})]
    return reviews, code_changes


def load_document_versions(tx, document_id):
    return [dict(row) for row in tx.run("""
        MATCH (d) WHERE elementId(d) = $id
        MATCH (d)-[:HAS_DOCUMENT_VERSION]->(v:DocumentVersion)
        RETURN elementId(v) AS id, v.display_name AS display_name, v.author_name AS author_name,
               v.version_at AS version_at, v.title AS title, v.body AS body,
               v.change_summary AS change_summary
    """, {"id": document_id})]


def load_meeting_segments(tx, segment_node_id):
    """The parent meeting and every segment it has, in `sequence_number` order.

    A meeting is a single conversation; half of it is worse than none, because
    the model will infer the missing half.
    """
    meeting_row = tx.run("""
        MATCH (m:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(s)
        WHERE elementId(s) = $id
        RETURN elementId(m) AS id, m.meeting_id AS meeting_id, m.title AS title,
               m.started_at AS started_at, m.ended_at AS ended_at
    """, {"id": segment_node_id}).single()
    if not meeting_row:
        return None, []

    segments = [dict(row) for row in tx.run("""
        MATCH (m) WHERE elementId(m) = $meeting_id
        MATCH (m)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(s:TeamsTranscriptSegment)
        RETURN elementId(s) AS id, s.segment_id AS segment_id, s.speaker_name AS speaker_name,
               s.body AS body, s.sequence_number AS sequence_number
        ORDER BY s.sequence_number
    """, {"meeting_id": meeting_row["id"]})]
    return dict(meeting_row), segments


def to_bundle_item(label, props, node_id):
    """One bundle entry: a stable identifier, display data, and full text."""
    if label == "MailMessage":
        identifier = props.get("message_id")
        text = "\n".join(filter(None, [props.get("subject"), props.get("body")]))
        return {"identifier": identifier, "label": label, "display_name": props.get("subject") or identifier,
                "author": props.get("sender_name"), "occurred_at": props.get("sent_at"), "text": text,
                "node_id": node_id}
    if label == "SlackMessage":
        identifier = f'{props.get("message_id")} v{props.get("version_number")}'
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": props.get("author_name"),
                "occurred_at": props.get("sent_at") or props.get("version_at"),
                "text": props.get("body") or "", "node_id": node_id}
    if label == "TeamsMeeting":
        identifier = props.get("meeting_id")
        return {"identifier": identifier, "label": label, "display_name": props.get("title") or identifier,
                "author": None, "occurred_at": props.get("started_at"),
                "text": props.get("title") or "", "node_id": node_id}
    if label == "TeamsTranscriptSegment":
        identifier = props.get("segment_id")
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": props.get("speaker_name"), "occurred_at": None,
                "text": props.get("body") or "", "node_id": node_id}
    if label == "Issue":
        identifier = props.get("issue_key")
        text = "\n".join(filter(None, [props.get("title"), props.get("description")]))
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": props.get("creator_name"), "occurred_at": props.get("created_at"),
                "text": text, "node_id": node_id}
    if label == "IssueVersion":
        identifier = props.get("display_name")
        text = "\n".join(filter(None, [props.get("title"), props.get("description"), props.get("acceptance_criteria")]))
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": props.get("changed_by_name"), "occurred_at": props.get("version_at"),
                "text": text, "node_id": node_id}
    if label == "IssueComment":
        identifier = props.get("comment_id")
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": props.get("author_name"),
                "occurred_at": props.get("created_at") or props.get("version_at"),
                "text": props.get("body") or "", "node_id": node_id}
    if label == "Document":
        identifier = props.get("document_id")
        text = "\n".join(filter(None, [props.get("title"), props.get("body")]))
        return {"identifier": identifier, "label": label, "display_name": props.get("title") or identifier,
                "author": props.get("author_name"), "occurred_at": props.get("created_at"),
                "text": text, "node_id": node_id}
    if label == "DocumentVersion":
        identifier = props.get("display_name")
        text = "\n".join(filter(None, [props.get("title"), props.get("body"), props.get("change_summary")]))
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": props.get("author_name"), "occurred_at": props.get("version_at"),
                "text": text, "node_id": node_id}
    if label == "PullRequest":
        identifier = props.get("display_name")
        text = "\n".join(filter(None, [props.get("title"), props.get("description")]))
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": props.get("author_name"), "occurred_at": props.get("created_at"),
                "text": text, "node_id": node_id}
    if label == "PullRequestReview":
        identifier = props.get("source_id") or props.get("display_name")
        return {"identifier": identifier, "label": label, "display_name": props.get("display_name") or identifier,
                "author": props.get("author_name"),
                "occurred_at": props.get("created_at") or props.get("version_at"),
                "text": props.get("body") or "", "node_id": node_id}
    if label == "CodeChange":
        identifier = props.get("display_name")
        text = "\n".join(filter(None, [
            f'{props.get("file_path", "")} ({props.get("change_type", "")})',
            props.get("before_summary"), props.get("after_summary"),
        ]))
        return {"identifier": identifier, "label": label, "display_name": identifier,
                "author": None, "occurred_at": None, "text": text, "node_id": node_id}

    identifier = props.get("display_name") or props.get("name") or node_id
    return {"identifier": identifier, "label": label, "display_name": identifier,
            "author": None, "occurred_at": None, "text": "", "node_id": node_id}


def assemble_bundle(tx, issue_key):
    """Build the evidence bundle for one topic candidate (one `Issue`)."""
    issue_row = load_issue_basics(tx, issue_key)
    if not issue_row:
        return None

    items = {}

    def add(label, props, node_id):
        item = to_bundle_item(label, props, node_id)
        return items.setdefault(item["identifier"], item)

    add("Issue", issue_row, issue_row["id"])
    own_ids = [issue_row["id"]]

    for version in load_issue_versions(tx, issue_key):
        add("IssueVersion", version, version["id"])
        own_ids.append(version["id"])

    for comment in load_issue_comments(tx, issue_key):
        add("IssueComment", comment, comment["id"])
        own_ids.append(comment["id"])

    for row in load_reference_connected(tx, own_ids):
        label = row["labels"][0] if row["labels"] else None
        props = row["props"]
        node_id = row["id"]

        if label == "TeamsTranscriptSegment":
            meeting, segments = load_meeting_segments(tx, node_id)
            if meeting:
                add("TeamsMeeting", meeting, meeting["id"])
                for segment in segments:
                    segment_item = add("TeamsTranscriptSegment", segment, segment["id"])
                    if segment_item.get("occurred_at") is None:
                        segment_item["occurred_at"] = meeting.get("started_at")
            else:
                add(label, props, node_id)
            continue

        add(label, props, node_id)

        if label == "PullRequest":
            reviews, code_changes = load_pull_request_children(tx, node_id)
            for review in reviews:
                add("PullRequestReview", review, review["id"])
            for change in code_changes:
                add("CodeChange", change, change["id"])
        elif label == "Document":
            for version in load_document_versions(tx, node_id):
                add("DocumentVersion", version, version["id"])

    ordered = sorted(
        items.values(),
        key=lambda item: (item["occurred_at"] is None, item["occurred_at"] or "", item["identifier"]),
    )

    return {
        "issue": issue_row,
        "items": ordered,
        "valid_identifiers": {item["identifier"] for item in ordered},
        "node_id_by_identifier": {item["identifier"]: item["node_id"] for item in ordered},
    }


# ---------------------------------------------------------------------------
# The model call (section 5).
# ---------------------------------------------------------------------------

def call_model(client, bundle, known_topics):
    payload = {
        "issue": {
            "issue_key": bundle["issue"]["issue_key"],
            "title": bundle["issue"]["title"],
            "status": bundle["issue"]["status"],
            "created_at": bundle["issue"]["created_at"],
        },
        "existing_topics": known_topics,
        "bundle_items_in_chronological_order": [
            {
                "identifier": item["identifier"],
                "label": item["label"],
                "display_name": item["display_name"],
                "author": item["author"],
                "occurred_at": item["occurred_at"],
                "text": item["text"],
            }
            for item in bundle["items"]
        ],
    }

    return client.responses.parse(
        model=OPENAI_MODEL,
        reasoning={"effort": OPENAI_REASONING_EFFORT},
        instructions=EXTRACTION_INSTRUCTIONS,
        input=json.dumps(payload, ensure_ascii=False, default=str),
        text_format=TopicEventExtractionOut,
    )


# ---------------------------------------------------------------------------
# The resolution layer (section 6). Nothing the model returns reaches the
# graph without passing these checks.
# ---------------------------------------------------------------------------

def parses_as_timestamp(value):
    if not value:
        return False
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def resolve_topic(topic_out, known_topics):
    """Returns `(slug, is_new)`. `existing_topic_slug` is honoured only on an exact match."""
    if topic_out.existing_topic_slug and topic_out.existing_topic_slug in known_topics:
        return topic_out.existing_topic_slug, False
    if topic_out.slug in known_topics:
        return topic_out.slug, False
    return topic_out.slug, True


def apply_resolution(parsed, valid_identifiers, registry):
    """Runs checks 1-7 from section 6. Returns surviving events/links plus counters."""
    overflow_events = max(0, len(parsed.events) - MAX_EVENTS_PER_TOPIC)
    overflow_links = max(0, len(parsed.causal_links) - MAX_CAUSAL_LINKS_PER_TOPIC)
    discarded_evidence = 0

    surviving_events = {}
    for event in parsed.events[:MAX_EVENTS_PER_TOPIC]:
        kept_evidence = [identifier for identifier in event.evidence if identifier in valid_identifiers]
        discarded_evidence += len(event.evidence) - len(kept_evidence)
        if not kept_evidence:
            continue
        if not parses_as_timestamp(event.occurred_at):
            continue

        actor_keys = []
        for name in event.actor_names:
            key = registry.resolve_person_key(name=name)
            if key:
                actor_keys.append(key)

        surviving_events[event.slug] = {
            "slug": event.slug,
            "name": event.name,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "summary": event.summary,
            "evidence": kept_evidence,
            "actor_keys": actor_keys,
        }

    causal_links = []
    for link in parsed.causal_links[:MAX_CAUSAL_LINKS_PER_TOPIC]:
        if link.cause_event_slug == link.effect_event_slug:
            continue
        if link.cause_event_slug not in surviving_events or link.effect_event_slug not in surviving_events:
            continue
        kept_evidence = [identifier for identifier in link.evidence if identifier in valid_identifiers]
        discarded_evidence += len(link.evidence) - len(kept_evidence)
        if not kept_evidence:
            continue
        causal_links.append({
            "cause_event_slug": link.cause_event_slug,
            "effect_event_slug": link.effect_event_slug,
            "explanation": link.explanation,
            "evidence": kept_evidence,
        })

    return {
        "events": list(surviving_events.values()),
        "causal_links": causal_links,
        "discarded_evidence": discarded_evidence,
        "overflow_events": overflow_events,
        "overflow_links": overflow_links,
    }


# ---------------------------------------------------------------------------
# Graph writes (section 7).
# ---------------------------------------------------------------------------

def ensure_knowledge_constraints(tx):
    tx.run("""
        CREATE CONSTRAINT topic_slug IF NOT EXISTS
        FOR (t:Topic) REQUIRE t.slug IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT event_key IF NOT EXISTS
        FOR (e:Event) REQUIRE (e.topic_slug, e.slug) IS UNIQUE
    """)


def delete_generated_relationships(tx):
    result = tx.run("""
        MATCH ()-[r]->() WHERE r.generated_by = $version
        DELETE r
        RETURN count(r) AS deleted
    """, {"version": EXTRACTION_VERSION})
    return result.single()["deleted"]


def delete_generated_nodes(tx):
    result = tx.run("""
        MATCH (n) WHERE n.generated_by = $version
        DETACH DELETE n
        RETURN count(n) AS deleted
    """, {"version": EXTRACTION_VERSION})
    return result.single()["deleted"]


def write_topic(tx, props):
    tx.run("""
        MERGE (t:Topic {slug: $slug})
        SET t.name = $name, t.topic_type = $topic_type, t.summary = $summary,
            t.derived = true, t.generated_by = $generated_by, t.model = $model,
            t.generated_at = $generated_at, t.display_name = $name
    """, props)


def write_about_topic(tx, issue_key, topic_slug, generated_at):
    tx.run("""
        MATCH (i:Issue {issue_key: $issue_key})
        MATCH (t:Topic {slug: $topic_slug})
        MERGE (i)-[r:ABOUT_TOPIC]->(t)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"issue_key": issue_key, "topic_slug": topic_slug,
          "version": EXTRACTION_VERSION, "generated_at": generated_at})


def write_derived_from(tx, topic_slug, node_ids, generated_at):
    if not node_ids:
        return
    tx.run("""
        MATCH (t:Topic {slug: $topic_slug})
        UNWIND $node_ids AS nid
        MATCH (n) WHERE elementId(n) = nid
        MERGE (t)-[r:DERIVED_FROM]->(n)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"topic_slug": topic_slug, "node_ids": node_ids,
          "version": EXTRACTION_VERSION, "generated_at": generated_at})


def write_event(tx, topic_slug, event, generated_at):
    tx.run("""
        MATCH (t:Topic {slug: $topic_slug})
        MERGE (e:Event {topic_slug: $topic_slug, slug: $slug})
        SET e.name = $name, e.event_type = $event_type, e.occurred_at = $occurred_at,
            e.summary = $summary, e.derived = true, e.generated_by = $version,
            e.model = $model, e.generated_at = $generated_at, e.display_name = $name
        MERGE (e)-[r:EVENT_OF_TOPIC]->(t)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"topic_slug": topic_slug, "slug": event["slug"], "name": event["name"],
          "event_type": event["event_type"], "occurred_at": event["occurred_at"],
          "summary": event["summary"], "version": EXTRACTION_VERSION,
          "model": OPENAI_MODEL, "generated_at": generated_at})


def write_evidenced_by(tx, topic_slug, event_slug, node_ids, generated_at):
    if not node_ids:
        return
    tx.run("""
        MATCH (e:Event {topic_slug: $topic_slug, slug: $slug})
        UNWIND $node_ids AS nid
        MATCH (n) WHERE elementId(n) = nid
        MERGE (e)-[r:EVIDENCED_BY]->(n)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"topic_slug": topic_slug, "slug": event_slug, "node_ids": node_ids,
          "version": EXTRACTION_VERSION, "generated_at": generated_at})


def write_acted_in_event(tx, person_key, topic_slug, event_slug, generated_at):
    tx.run("""
        MATCH (p:Person {person_key: $person_key})
        MATCH (e:Event {topic_slug: $topic_slug, slug: $slug})
        MERGE (p)-[r:ACTED_IN_EVENT]->(e)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"person_key": person_key, "topic_slug": topic_slug, "slug": event_slug,
          "version": EXTRACTION_VERSION, "generated_at": generated_at})


def write_caused(tx, topic_slug, link, generated_at):
    tx.run("""
        MATCH (cause:Event {topic_slug: $topic_slug, slug: $cause_slug})
        MATCH (effect:Event {topic_slug: $topic_slug, slug: $effect_slug})
        MERGE (cause)-[r:CAUSED]->(effect)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at,
            r.explanation = $explanation, r.evidence = $evidence
    """, {"topic_slug": topic_slug, "cause_slug": link["cause_event_slug"],
          "effect_slug": link["effect_event_slug"], "version": EXTRACTION_VERSION,
          "generated_at": generated_at, "explanation": link["explanation"],
          "evidence": link["evidence"]})


# ---------------------------------------------------------------------------
# Pipeline state and results (section 8).
# ---------------------------------------------------------------------------

def read_pipeline_state_full(tx):
    # Reads every stage's timestamp (not just this layer's own upstream) so
    # that staleness can be computed transitively. See `pipeline_staleness.py`.
    return _read_full_pipeline_state(tx)


def load_knowledge_state(tx):
    topics = [dict(row) for row in tx.run("""
        MATCH (t:Topic)
        OPTIONAL MATCH (t)<-[:EVENT_OF_TOPIC]-(e:Event)
        RETURN t.slug AS slug, t.name AS name, t.topic_type AS topic_type,
               t.summary AS summary, count(e) AS event_count
        ORDER BY t.slug
    """)]

    events = [dict(row) for row in tx.run("""
        MATCH (e:Event)-[:EVENT_OF_TOPIC]->(t:Topic)
        OPTIONAL MATCH (e)-[:EVIDENCED_BY]->(ev)
        OPTIONAL MATCH (p:Person)-[:ACTED_IN_EVENT]->(e)
        WITH e, t, collect(DISTINCT coalesce(ev.display_name, ev.name, '')) AS evidence,
             collect(DISTINCT p.name) AS actors
        RETURN t.slug AS topic_slug, e.slug AS slug, e.name AS name, e.event_type AS event_type,
               e.occurred_at AS occurred_at, e.summary AS summary, evidence, actors
        ORDER BY t.slug, e.occurred_at
    """)]

    causal_links = [dict(row) for row in tx.run("""
        MATCH (cause:Event)-[r:CAUSED]->(effect:Event)
        RETURN cause.topic_slug AS topic_slug, cause.slug AS cause_slug, cause.name AS cause_name,
               effect.slug AS effect_slug, effect.name AS effect_name,
               r.explanation AS explanation, r.evidence AS evidence
        ORDER BY topic_slug, cause_slug
    """)]

    state = read_pipeline_state_full(tx)
    staleness = compute_staleness(state)["knowledge"]
    return {
        "topics": topics,
        "events": events,
        "causal_links": causal_links,
        "last_extraction_at": state["last_extraction_at"],
        "last_layer_build_at": state["last_layer_build_at"],
        "needs_layer_rerun": staleness["stale"],
        "stale_reasons": staleness["reasons"],
    }


def knowledge_state_payload(session):
    return session.execute_read(load_knowledge_state)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def run_topic_event_extraction(session, database_url):
    client = require_openai_client()
    registry = build_registry(database_url)

    generated_at = now_iso()

    session.execute_write(ensure_knowledge_constraints)
    deleted_relationships = session.execute_write(delete_generated_relationships)
    deleted_nodes = session.execute_write(delete_generated_nodes)

    issue_keys = session.execute_read(load_topic_candidates)

    known_topics = {}
    calls = 0
    input_tokens = 0
    output_tokens = 0
    has_token_usage = False
    total_discarded_evidence = 0
    total_overflow_events = 0
    total_overflow_links = 0

    for issue_key in issue_keys:
        bundle = session.execute_read(assemble_bundle, issue_key)
        if not bundle:
            continue

        response = call_model(
            client, bundle,
            [{"slug": slug, "name": name} for slug, name in known_topics.items()],
        )
        calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            has_token_usage = True
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens

        parsed = response.output_parsed
        resolved = apply_resolution(parsed, bundle["valid_identifiers"], registry)
        total_discarded_evidence += resolved["discarded_evidence"]
        total_overflow_events += resolved["overflow_events"]
        total_overflow_links += resolved["overflow_links"]

        topic_slug, is_new = resolve_topic(parsed.topic, known_topics)
        topic_name = parsed.topic.name if is_new else known_topics[topic_slug]
        if is_new:
            known_topics[topic_slug] = parsed.topic.name

        session.execute_write(write_topic, {
            "slug": topic_slug,
            "name": topic_name,
            "topic_type": parsed.topic.topic_type,
            "summary": parsed.topic.summary,
            "generated_by": EXTRACTION_VERSION,
            "model": OPENAI_MODEL,
            "generated_at": generated_at,
        })
        session.execute_write(write_about_topic, issue_key, topic_slug, generated_at)

        node_id_by_identifier = bundle["node_id_by_identifier"]
        session.execute_write(
            write_derived_from, topic_slug,
            [node_id_by_identifier[item["identifier"]] for item in bundle["items"]],
            generated_at,
        )

        for event in resolved["events"]:
            session.execute_write(write_event, topic_slug, event, generated_at)
            evidence_ids = [node_id_by_identifier[e] for e in event["evidence"] if e in node_id_by_identifier]
            session.execute_write(write_evidenced_by, topic_slug, event["slug"], evidence_ids, generated_at)
            for actor_key in event["actor_keys"]:
                session.execute_write(write_acted_in_event, actor_key, topic_slug, event["slug"], generated_at)

        for link in resolved["causal_links"]:
            session.execute_write(write_caused, topic_slug, link, generated_at)

    session.execute_write(touch_pipeline_state, "last_layer_build_at", generated_at)

    report = session.execute_read(load_knowledge_state)
    report.update({
        "built_at": generated_at,
        "deleted_relationships": deleted_relationships,
        "deleted_nodes": deleted_nodes,
        "calls": calls,
        "model": OPENAI_MODEL,
        "discarded_evidence": total_discarded_evidence,
        "overflow_events": total_overflow_events,
        "overflow_links": total_overflow_links,
        "token_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens} if has_token_usage else None,
    })
    return report


def build_knowledge_layer(session):
    return run_topic_event_extraction(session, postgres_database_url())
