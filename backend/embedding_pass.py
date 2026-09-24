"""On-demand embedding pass over the Neo4j graph.

Every layer gets at least one entry point. For each embedded label, a
read-only Cypher query collects the node together with the context the
earlier layers built around it (causal explanations, dependencies, root
causes, expertise, collaboration, communities, bus factor), and a text
builder turns that into one text. The text is embedded with OpenAI
`text-embedding-3-large` and stored on the node together with the text
itself, and the node gets the extra label `Searchable`, which carries one
vector index and one fulltext index across all layers.

Built to keep working as the data grows: every assembled list is capped,
a text too long for the model is split into `EmbeddingChunk` nodes, API
batches are sized by text length, transient API errors are retried, and
every embedded node records which source it belongs to and whether it is
the latest version, so search can group versions.

The pass never changes nodes, relationships or properties written by other
layers. On their nodes it writes only the properties in
`EMBEDDING_PROPERTIES` and the `Searchable` label. The only nodes it
creates are its own `EmbeddingChunk` nodes. It is incremental: a node is
re-embedded only when its text, group, latest flag, the model or the
version changed, unless `force=True`.

See `docs/EMBEDDING_LAYER_HANDOFF.md` for the design behind every choice.
"""

import hashlib
import re
from typing import Callable, Optional

from causal_layer import source_identifier
from reference_extraction import EXTRACTOR_NAME as REFERENCE_EXTRACTOR_NAME
from reference_extraction import PIPELINE_STATE_ID, PIPELINE_STATE_LABEL
from reference_extraction import document_identifier, now_iso, touch_pipeline_state
from topic_event_extraction import require_openai_client
from pipeline_staleness import compute_staleness
from pipeline_staleness import read_pipeline_state as _read_full_pipeline_state

EMBEDDING_VERSION = "embedding-v3"
EMBEDDING_PROVIDER = "OpenAI"
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1536
EMBEDDING_SIMILARITY = "cosine"

# API batching and retries. OpenAI accepts up to 2048 inputs and about
# 300 000 tokens per request; the character budget stays well below that
# even for dense text (about 2 characters per token).
BATCH_MAX_INPUTS = 100
BATCH_MAX_CHARS = 200_000
API_MAX_RETRIES = 6          # the OpenAI client backs off exponentially on 429, 5xx and timeouts
API_TIMEOUT_SECONDS = 120

# Chunking. The model reads at most 8191 tokens per text. A text longer than
# MAX_TEXT_CHARS is split: its first line (what the node is) is repeated at the
# top of every part, and the rest is cut into overlapping windows.
MAX_TEXT_CHARS = 12_000
CHUNK_BODY_CHARS = 6_000
CHUNK_OVERLAP_CHARS = 500

# Token estimate for the preview (English text averages about 4 characters per token).
CHARS_PER_TOKEN_ESTIMATE = 4

SEARCHABLE_LABEL = "Searchable"
CHUNK_LABEL = "EmbeddingChunk"
CHUNK_RELATIONSHIP = "CHUNK_OF"
# Chunk nodes carry `generated_by = <EMBEDDING_VERSION>`; queries find them by this
# prefix rather than by label, so Neo4j does not warn before the first chunk exists.
CHUNK_GENERATED_BY_PREFIX = "embedding-"
VECTOR_INDEX_NAME = "searchable_embedding"
FULLTEXT_INDEX_NAME = "searchable_text"

# Per-label vector indexes from embedding-v1, replaced by `searchable_embedding`.
LEGACY_VECTOR_INDEXES = [
    "mailmessage_embedding", "slackmessage_embedding", "teamstranscriptsegment_embedding",
    "issueversion_embedding", "issuecomment_embedding", "documentversion_embedding",
    "pullrequestreview_embedding", "pullrequest_embedding", "topic_embedding",
    "event_embedding", "component_embedding", "rootcause_embedding",
]

FULLTEXT_ENTITY_LABELS = ["Person", "Issue", "Document", "PullRequest", "Topic"]
FULLTEXT_ENTITY_PROPERTIES = ["name", "display_name", "title"]

EMBEDDING_PROPERTIES = [
    "embedding", "embedding_text", "embedding_model", "embedding_source_hash",
    "embedding_version", "embedding_group", "embedding_is_latest", "embedding_parts", "embedded_at",
]

ELIGIBLE_PERSON_WHERE = "coalesce(n.actor_type, '') <> 'mailbox' AND coalesce(n.identity_ambiguous, false) <> true"

# Thresholds for the plain-language sentences written from numeric metrics.
BRIDGE_BETWEENNESS = 0.3
QUOTE_CHARS = 200

# Caps on every assembled list. When a list is longer, the most important
# entries are kept (highest share or weight, most recent) and the text says
# how many were left out, so a hub node's vector stays focused.
MAX_RECIPIENTS = 20
MAX_MENTIONS = 20
MAX_FILES = 20
MAX_ISSUES = 20
MAX_ACTORS = 15
MAX_CAUSAL_LINKS = 10          # per direction, per event
MAX_ROOT_CAUSES = 10
MAX_COMPONENT_LINKS = 10
MAX_CODE_LINKS = 10
MAX_EVIDENCE = 20
MAX_EXPERTS = 10
MAX_DEPENDENCIES = 10          # per direction, per component
MAX_AFFECTING_EVENTS = 15
MAX_EXPLAINED_EVENTS = 15
MAX_EXPERTISE_LINES = 10
MAX_WORKS_WITH = 10
MAX_COMMUNITY_PEERS = 15
MAX_COMMUNITY_MEMBERS = 30
MAX_COMMUNITY_WORK_ITEMS = 20

_HEADING_PATTERN = re.compile(r"(?m)^#{1,6}[ \t]+")


# ---------------------------------------------------------------------------
# Text helpers.
# ---------------------------------------------------------------------------

def _strip_markdown_headings(text: str) -> str:
    """`## Background` -> `Background`. Nothing else is touched."""
    return _HEADING_PATTERN.sub("", text)


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _line(prefix: str, value) -> Optional[str]:
    """`Prefix: value`, or nothing when the value is empty."""
    value = _clean(value)
    return f"{prefix}: {value}" if value else None


def _join_lines(lines: list[Optional[str]]) -> str:
    return "\n".join(line for line in lines if line and line.strip())


def _time(value) -> str:
    """`2026-03-03T14:25:00+01:00` -> `2026-03-03 14:25`."""
    if value is None:
        return ""
    text = value.iso_format() if hasattr(value, "iso_format") else str(value)
    return text[:16].replace("T", " ")


def _quote(value) -> str:
    text = " ".join(_clean(value).split())
    if len(text) > QUOTE_CHARS:
        text = text[:QUOTE_CHARS].rstrip() + "..."
    return f'"{text}"' if text else ""


def _percent(share) -> str:
    if share is None:
        return "-"
    return f"{round(float(share) * 100, 1):g} %"


def _more(count: int) -> str:
    return f"... and {count} more" if count > 0 else ""


def _names(values, limit: int) -> str:
    """Distinct, sorted, comma-joined names, capped at `limit`."""
    names = sorted({_clean(value) for value in values or [] if _clean(value)})
    shown = ", ".join(names[:limit])
    rest = len(names) - limit
    return f"{shown}, {_more(rest)}" if rest > 0 else shown


def _present(rows, field="name") -> list[dict]:
    """Drops the all-null rows that OPTIONAL MATCH + collect({..}) produces."""
    return [row for row in rows or [] if row and row.get(field) is not None]


def _top(rows: list[dict], limit: int, importance: Optional[Callable[[dict], object]] = None):
    """Keeps the `limit` most important rows (or the first by name). Returns (rows, left out)."""
    ordered = sorted(rows, key=importance) if importance else list(rows)
    return ordered[:limit], max(0, len(ordered) - limit)


def _more_line(count: int, what: str) -> Optional[str]:
    return f"... and {count} more {what}" if count > 0 else None


def _mentions_line(mentions) -> Optional[str]:
    targets = []
    for row in mentions or []:
        kind = row.get("type")
        if kind == "MENTIONS_ISSUE":
            targets.append(row.get("issue_key"))
        elif kind == "MENTIONS_PULL_REQUEST":
            targets.append(row.get("display_name"))
        elif kind == "MENTIONS_DOCUMENT":
            targets.append(document_identifier(row.get("title")) or row.get("document_id"))
    return _line("Mentions", _names(targets, MAX_MENTIONS))


def _evidence_line(evidence) -> Optional[str]:
    identifiers = []
    for row in evidence or []:
        if not row or not row.get("label"):
            continue
        props = row.get("props") or {}
        if row["label"] == "CodeChange":
            identifiers.append(f'{props.get("display_name")} v{props.get("version_number")}')
        else:
            identifiers.append(source_identifier(row["label"], props))
    return _line("Evidence", _names(identifiers, MAX_EVIDENCE))


def _experts_line(experts) -> Optional[str]:
    rows, rest = _top(_present(experts, "person"), MAX_EXPERTS, lambda row: -(row.get("share") or 0))
    rows = sorted(rows, key=lambda row: (row.get("rank") or 0, row["person"]))
    parts = [f'{row["person"]} ({_percent(row.get("share"))}, rank {row.get("rank")})' for row in rows]
    if rest:
        parts.append(_more(rest))
    return _line("Experts", ", ".join(parts))


def _bus_factor_line(bus_factor, top_expert) -> Optional[str]:
    if bus_factor is None:
        return None
    if bus_factor == 0:
        risk = "no one has recorded expertise"
    elif bus_factor == 1:
        risk = "knowledge risk, one person holds most of the expertise"
    else:
        risk = f"expertise is shared by {bus_factor} people"
    top = f"; top expert {top_expert}" if top_expert else ""
    return f"Bus factor {bus_factor}: {risk}{top}."


def _explained_lines(prefix: str, rows, limit: int, what: str, name_field="name") -> list[Optional[str]]:
    """`Prefix: Name - explanation` per row, sorted by name, capped at `limit`."""
    rows, rest = _top(sorted(_present(rows, name_field), key=lambda row: _clean(row[name_field])), limit)
    lines = []
    for row in rows:
        explanation = _clean(row.get("explanation"))
        name = _clean(row[name_field])
        lines.append(f"{prefix}: {name} - {explanation}" if explanation else f"{prefix}: {name}")
    lines.append(_more_line(rest, what))
    return lines


# ---------------------------------------------------------------------------
# Text builders. Each takes one row from its label's query (section 5 of the
# handoff) and returns the text to embed.
# ---------------------------------------------------------------------------

def _mail_message_text(row):
    props = row["props"]
    recipients = _names(row["recipients"], MAX_RECIPIENTS)
    return _join_lines([
        f'Email from {_clean(props.get("sender_name")) or _clean(props.get("sender_address"))}'
        + (f" to {recipients}" if recipients else "")
        + (f', {_time(props.get("sent_at"))}' if props.get("sent_at") else ""),
        _line("Subject", props.get("subject")),
        _line("In reply to", _quote(row["parent_subject"])),
        _clean(props.get("body")),
        _mentions_line(row["mentions"]),
    ])


def _slack_message_text(row):
    props = row["props"]
    return _join_lines([
        f'Slack message in #{_clean(props.get("channel_name"))} by {_clean(props.get("author_name"))}, '
        f'{_time(props.get("sent_at"))}, version {props.get("version_number")} of {row["version_count"]}',
        _line("Reply to", _quote(row["reply_to"])),
        _clean(props.get("body")),
        _mentions_line(row["mentions"]),
    ])


def _teams_transcript_segment_text(row):
    props = row["props"]
    previous = (
        f'Previous line ({_clean(row["previous_speaker"])}): {_quote(row["previous_body"])}'
        if row["previous_body"] else None
    )
    return _join_lines([
        f'Meeting transcript: {_clean(row["meeting_title"])}, {_time(row["meeting_started_at"])}, '
        f'speaker {_clean(props.get("speaker_name"))}',
        previous,
        _clean(props.get("body")),
        _mentions_line(row["mentions"]),
    ])


def _issue_version_text(row):
    props = row["props"]
    state = ", ".join(part for part in [
        _line("Status", props.get("status")),
        _line("priority", props.get("priority")),
        _line("assignee", props.get("assignee_name")),
        _line("changed by", props.get("changed_by_name")),
    ] if part)
    return _join_lines([
        f'Issue {_clean(row["issue_key"])} version {props.get("version_number")} of {row["version_count"]}, '
        f'{_time(props.get("version_at"))}',
        state,
        _line("Title", props.get("title")),
        _line("Description", props.get("description")),
        _line("Acceptance criteria", props.get("acceptance_criteria")),
        _mentions_line(row["mentions"]),
    ])


def _issue_comment_text(row):
    props = row["props"]
    return _join_lines([
        f'Comment on issue {_clean(row["issue_key"])} by {_clean(props.get("author_name"))}, '
        f'{_time(props.get("created_at"))}',
        _line("Reply to", _quote(row["reply_to"])),
        _clean(props.get("body")),
        _mentions_line(row["mentions"]),
    ])


def _document_version_text(row):
    props = row["props"]
    body = props.get("body")
    if body:
        body = _strip_markdown_headings(body)
    identifier = document_identifier(props.get("title")) or props.get("document_id")
    return _join_lines([
        f'Document {_clean(identifier)} ({_clean(props.get("document_type"))}) version {props.get("version_number")} '
        f'of {row["version_count"]} by {_clean(props.get("author_name"))}, {_time(props.get("version_at"))}',
        _line("Title", props.get("title")),
        _line("Change summary", props.get("change_summary")),
        _clean(body),
        _mentions_line(row["mentions"]),
    ])


def _pull_request_text(row):
    props = row["props"]
    return _join_lines([
        f'Pull request {_clean(props.get("repository"))}#{props.get("pr_number")} by '
        f'{_clean(props.get("author_name"))}, state {_clean(props.get("state"))}',
        _line("Title", props.get("title")),
        _line("Description", props.get("description")),
        _line("Changed files", _names(row["files"], MAX_FILES)),
        _mentions_line(row["mentions"]),
    ])


def _pull_request_review_text(row):
    props = row["props"]
    location = None
    if props.get("file_path"):
        line_part = f':{props.get("line_number")}' if props.get("line_number") is not None else ""
        side_part = f' ({props.get("diff_side")})' if props.get("diff_side") else ""
        location = f'Location: {props.get("file_path")}{line_part}{side_part}'
    return _join_lines([
        f'Pull request review on {_clean(props.get("repository"))}#{props.get("pr_number")} '
        f'(PR version {props.get("pr_version_number")}) by {_clean(props.get("author_name"))}, '
        f'{_time(props.get("created_at"))}',
        _line("Type", props.get("entry_type")),
        location,
        _line("Reply to", _quote(row["reply_to"])),
        _clean(props.get("body")),
        _mentions_line(row["mentions"]),
    ])


def _code_change_text(row):
    props = row["props"]
    return _join_lines([
        f'Code change in {_clean(props.get("repository"))}#{props.get("pr_number")} version '
        f'{props.get("version_number")}: {_clean(props.get("file_path"))} ({_clean(props.get("change_type"))})',
        _line("Pull request", row["pr_title"]),
        _line("Before", props.get("before_summary")),
        _line("After", props.get("after_summary")),
        # The imported diffs carry escaped newlines (`\n` as two characters).
        _line("Diff", _clean(props.get("diff")).replace("\\n", "\n")),
        _mentions_line(row["mentions"]),
    ])


def _topic_text(row):
    props = row["props"]
    events = None
    if row["event_count"]:
        events = f'Events: {row["event_count"]}, from {_time(row["first_event_at"])} to {_time(row["last_event_at"])}'
    return _join_lines([
        f'Topic: {_clean(props.get("name"))} ({_clean(props.get("topic_type"))})',
        _line("Issues", _names(row["issues"], MAX_ISSUES)),
        _clean(props.get("summary")),
        events,
        _experts_line(row["experts"]),
        _bus_factor_line(props.get("bus_factor"), props.get("top_expert")),
    ])


def _event_text(row):
    props = row["props"]
    code_rows, code_rest = _top(
        sorted(_present(row["code"]), key=lambda code: (_clean(code["name"]), code.get("version") or 0)),
        MAX_CODE_LINKS,
    )
    code_lines = []
    for code in code_rows:
        explanation = _clean(code.get("explanation"))
        code_lines.append(
            f'Contributing code: {code["name"]} v{code.get("version")} ({_clean(code.get("type"))})'
            + (f" - {explanation}" if explanation else "")
        )
    code_lines.append(_more_line(code_rest, "contributing code changes"))
    return _join_lines([
        f'Event: {_clean(props.get("name"))} ({_clean(props.get("event_type"))}), {_time(props.get("occurred_at"))}',
        _line("Topic", row["topic_name"]),
        _line("Actors", _names(row["actors"], MAX_ACTORS)),
        _clean(props.get("summary")),
        *_explained_lines("Caused by", row["caused_by"], MAX_CAUSAL_LINKS, "causes"),
        *_explained_lines("Led to", row["led_to"], MAX_CAUSAL_LINKS, "effects"),
        *_explained_lines("Root cause", row["root_causes"], MAX_ROOT_CAUSES, "root causes"),
        *_explained_lines("Affected component", row["components"], MAX_COMPONENT_LINKS, "affected components"),
        *code_lines,
        _evidence_line(row["evidence"]),
    ])


def _dependency_lines(prefix: str, rows, what: str) -> list[Optional[str]]:
    rows, rest = _top(sorted(_present(rows), key=lambda dep: _clean(dep["name"])), MAX_DEPENDENCIES)
    lines = [
        f'{prefix}: {dep["name"]} ({_clean(dep.get("type"))})'
        + (f' - {_clean(dep.get("explanation"))}' if _clean(dep.get("explanation")) else "")
        for dep in rows
    ]
    lines.append(_more_line(rest, what))
    return lines


def _component_text(row):
    props = row["props"]
    # The most recent affecting events are kept, then listed by name.
    events = sorted(_present(row["events"]), key=lambda event: _clean(event.get("at")), reverse=True)
    events, events_rest = events[:MAX_AFFECTING_EVENTS], max(0, len(events) - MAX_AFFECTING_EVENTS)
    event_names = sorted({_clean(event["name"]) for event in events})
    affected = ", ".join(event_names) + (f", {_more(events_rest)}" if events_rest else "")
    return _join_lines([
        f'Component: {_clean(props.get("name"))} ({_clean(props.get("component_type"))}) '
        f'in repository {_clean(props.get("repository"))}',
        _clean(props.get("summary")),
        _line("Files", _names(row["files"], MAX_FILES)),
        *_dependency_lines("Depends on", row["depends_on"], "dependencies"),
        *_dependency_lines("Used by", row["used_by"], "dependents"),
        _line("Affected by events", affected),
        _line("Root causes located here", _names(row["root_causes"], MAX_ROOT_CAUSES)),
        _experts_line(row["experts"]),
        _bus_factor_line(props.get("bus_factor"), props.get("top_expert")),
    ])


def _root_cause_text(row):
    props = row["props"]
    return _join_lines([
        f'Root cause: {_clean(props.get("name"))} ({_clean(props.get("cause_type"))})',
        _clean(props.get("summary")),
        *_explained_lines("Explains", row["explains"], MAX_EXPLAINED_EVENTS, "explained events"),
        _line("Located in components", _names(row["components"], MAX_COMPONENT_LINKS)),
        _evidence_line(row["evidence"]),
    ])


def _person_text(row):
    props = row["props"]
    activity_parts = [
        (row["prs_authored"], "authored {} PR", "authored {} PRs"),
        (row["pr_reviews"], "wrote {} PR review", "wrote {} PR reviews"),
        (row["slack_messages"], "{} Slack message", "{} Slack messages"),
        (row["mails"], "{} email", "{} emails"),
        (row["issue_comments"], "{} issue comment", "{} issue comments"),
        (row["document_versions"], "{} document version", "{} document versions"),
        (row["segments"], "spoke in {} meeting segment", "spoke in {} meeting segments"),
        (row["events"], "acted in {} event", "acted in {} events"),
    ]
    activity = ", ".join(
        (one if count == 1 else many).format(count) for count, one, many in activity_parts if count
    )

    expertise, expertise_rest = _top(
        _present(row["expertise"], "subject"), MAX_EXPERTISE_LINES,
        lambda x: (-(x.get("share") or 0), x["subject"]),
    )
    expertise_lines = [
        f'Expertise: {x["subject"]} ({x.get("subject_label")}): {_percent(x.get("share"))}, rank {x.get("rank")}'
        for x in expertise
    ]
    expertise_lines.append(_more_line(expertise_rest, "areas of expertise"))

    partners, partners_rest = _top(
        _present(row["works_with"]), MAX_WORKS_WITH, lambda w: (-(w.get("weight") or 0), w["name"]),
    )
    works_with = ", ".join(
        f'{w["name"]} (weight {w.get("weight")}: {", ".join(sorted(w.get("types") or []))})' for w in partners
    ) + (f", {_more(partners_rest)}" if partners_rest else "")

    community = None
    if props.get("community_id"):
        others = _names(row["community_members"], MAX_COMMUNITY_PEERS)
        community = f'Community: {props["community_id"]}' + (f" with {others}" if others else "")

    network = None
    betweenness = props.get("collab_betweenness")
    if props.get("collab_weighted_degree") is not None or betweenness is not None:
        if betweenness is None:
            role = ""
        elif betweenness >= BRIDGE_BETWEENNESS:
            role = " (bridges otherwise separate groups)"
        elif betweenness > 0:
            role = " (sometimes connects others)"
        else:
            role = " (not a bridge between groups)"
        network = (
            f'Collaboration network: weighted degree {props.get("collab_weighted_degree")}, '
            f"betweenness {betweenness}{role}"
        )

    return _join_lines([
        f'Person: {_clean(props.get("name"))}',
        _line("Activity", activity),
        *expertise_lines,
        _line("Works with", works_with),
        community,
        network,
    ])


def _community_text(row):
    props = row["props"]
    types = sorted({item for types in row["work_item_types"] or [] for item in (types or [])})
    items = sorted({_clean(item) for items in row["work_items"] or [] for item in (items or []) if _clean(item)})
    shown_items = ", ".join(items[:MAX_COMMUNITY_WORK_ITEMS])
    items_rest = len(items) - MAX_COMMUNITY_WORK_ITEMS
    if items_rest > 0:
        shown_items += f", {_more(items_rest)}"
    return _join_lines([
        f'Community: {_clean(props.get("community_id"))}, {props.get("size")} members '
        f"(Louvain community detection on WORKS_WITH)",
        _line("Members", _names(row["members"], MAX_COMMUNITY_MEMBERS)),
        _line("Collaborate on", ", ".join(types)),
        _line("Shared work items", shown_items),
    ])


# ---------------------------------------------------------------------------
# Groups. `embedding_group` names the source a node belongs to, so search can
# fold several versions of one source into one hit; `embedding_is_latest`
# says whether the node is that source's latest version.
# ---------------------------------------------------------------------------

def _is_latest(row) -> bool:
    latest = row.get("latest_version")
    return latest is None or row["props"].get("version_number") == latest


def _single(group_fn):
    """A label whose node is its source's only (or latest) state."""
    return lambda row: (group_fn(row["props"]), True)


def _versioned(group_fn):
    return lambda row: (group_fn(row), _is_latest(row))


GROUPS = {
    "MailMessage": _single(lambda p: f'Mail {p.get("message_id")}'),
    "SlackMessage": _versioned(lambda row: f'Slack {row["props"].get("message_id")}'),
    "TeamsTranscriptSegment": _single(lambda p: f'Teams {p.get("meeting_id")}/{p.get("segment_id")}'),
    "IssueVersion": _versioned(lambda row: f'Issue {row["issue_key"] or row["props"].get("issue_id")}'),
    "IssueComment": _single(lambda p: f'Issue comment {p.get("comment_id")}'),
    "DocumentVersion": _versioned(lambda row: f'Document {row["props"].get("document_id")}'),
    "PullRequest": _single(lambda p: f'Pull request {p.get("repository")}#{p.get("pr_number")}'),
    "PullRequestReview": _single(
        lambda p: f'Pull request review {p.get("repository")}#{p.get("pr_number")}/{p.get("source_id")}'
    ),
    "CodeChange": _versioned(
        lambda row: f'Code change {row["props"].get("repository")}#{row["props"].get("pr_number")} '
                    f'{row["props"].get("file_path")}'
    ),
    "Topic": _single(lambda p: f'Topic {p.get("slug")}'),
    "Event": _single(lambda p: f'Event {p.get("topic_slug")}/{p.get("slug")}'),
    "Component": _single(lambda p: f'Component {p.get("repository")}/{p.get("slug")}'),
    "RootCause": _single(lambda p: f'Root cause {p.get("slug")}'),
    "Person": _single(lambda p: f'Person {p.get("person_key")}'),
    "Community": _single(lambda p: f'Community {p.get("community_id")}'),
}


# ---------------------------------------------------------------------------
# Read queries, one per label. Every query returns `id` (elementId), `props`
# (without the vector) and the context its text builder needs. They only read.
# ---------------------------------------------------------------------------

_MENTIONS = """
    CALL (n) {
        OPTIONAL MATCH (n)-[m]->(t)
        WHERE m.extracted_by = $reference_extractor
        RETURN collect({type: type(m), issue_key: t.issue_key, display_name: t.display_name,
                        title: t.title, document_id: t.document_id}) AS mentions
    }
"""

_EXPERTS = """
    CALL (n) {
        OPTIONAL MATCH (p:Person)-[:HAS_EXPERTISE]->(x:Expertise)-[:EXPERTISE_IN]->(n)
        RETURN collect({person: p.name, share: x.share, rank: x.rank}) AS experts
    }
"""

_EVIDENCE_PROPS = """{label: labels(s)[0], props: s {.issue_key, .display_name, .comment_id, .message_id,
                       .version_number, .meeting_id, .segment_id, .document_id, .source_id, .name}}"""

_PROPS = "n {.*, embedding: null, embedding_text: null} AS props"

QUERIES = {
    "MailMessage": f"""
        MATCH (n:MailMessage)
        CALL (n) {{
            OPTIONAL MATCH (n)-[:MAIL_RECIPIENT]->(r:Person)
            RETURN collect(DISTINCT r.name) AS recipients
        }}
        CALL (n) {{
            OPTIONAL MATCH (parent:MailMessage)
            WHERE parent.source_instance = n.source_instance AND parent.message_id = n.in_reply_to_id
            RETURN parent.subject AS parent_subject LIMIT 1
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, recipients, parent_subject, mentions
    """,
    "SlackMessage": f"""
        MATCH (n:SlackMessage)
        CALL (n) {{
            OPTIONAL MATCH (n)-[:SLACK_THREAD_REPLY_TO]->(root:SlackMessage)
            RETURN root.body AS reply_to ORDER BY root.version_number DESC LIMIT 1
        }}
        CALL (n) {{
            MATCH (v:SlackMessage)
            WHERE v.source_instance = n.source_instance AND v.workspace_id = n.workspace_id
              AND v.channel_id = n.channel_id AND v.message_id = n.message_id
            RETURN count(v) AS version_count, max(v.version_number) AS latest_version
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, reply_to, version_count, latest_version, mentions
    """,
    "TeamsTranscriptSegment": f"""
        MATCH (n:TeamsTranscriptSegment)
        CALL (n) {{
            OPTIONAL MATCH (m:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(n)
            OPTIONAL MATCH (m)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(prev:TeamsTranscriptSegment)
            WHERE prev.sequence_number = n.sequence_number - 1
            RETURN m.title AS meeting_title, m.started_at AS meeting_started_at,
                   prev.speaker_name AS previous_speaker, prev.body AS previous_body LIMIT 1
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, meeting_title, meeting_started_at,
               previous_speaker, previous_body, mentions
    """,
    "IssueVersion": f"""
        MATCH (n:IssueVersion)
        CALL (n) {{
            OPTIONAL MATCH (i:Issue)-[:HAS_ISSUE_VERSION]->(n)
            OPTIONAL MATCH (i)-[:HAS_ISSUE_VERSION]->(v:IssueVersion)
            RETURN i.issue_key AS issue_key, count(v) AS version_count, max(v.version_number) AS latest_version
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, issue_key, version_count, latest_version, mentions
    """,
    "IssueComment": f"""
        MATCH (n:IssueComment)
        CALL (n) {{
            OPTIONAL MATCH (i:Issue)-[:HAS_ISSUE_COMMENT]->(n)
            RETURN i.issue_key AS issue_key LIMIT 1
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[:REPLY_TO_ISSUE_COMMENT]->(parent:IssueComment)
            RETURN parent.body AS reply_to LIMIT 1
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, issue_key, reply_to, mentions
    """,
    "DocumentVersion": f"""
        MATCH (n:DocumentVersion)
        CALL (n) {{
            OPTIONAL MATCH (d:Document)-[:HAS_DOCUMENT_VERSION]->(n)
            OPTIONAL MATCH (d)-[:HAS_DOCUMENT_VERSION]->(v:DocumentVersion)
            RETURN count(v) AS version_count, max(v.version_number) AS latest_version
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, version_count, latest_version, mentions
    """,
    "PullRequest": f"""
        MATCH (n:PullRequest)
        CALL (n) {{
            OPTIONAL MATCH (n)-[:HAS_CODE_CHANGE]->(c:CodeChange)
            RETURN collect(DISTINCT c.file_path) AS files
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, files, mentions
    """,
    "PullRequestReview": f"""
        MATCH (n:PullRequestReview)
        CALL (n) {{
            OPTIONAL MATCH (n)-[:REPLY_TO_PR_REVIEW]->(parent:PullRequestReview)
            RETURN parent.body AS reply_to LIMIT 1
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, reply_to, mentions
    """,
    "CodeChange": f"""
        MATCH (n:CodeChange)
        CALL (n) {{
            OPTIONAL MATCH (pr:PullRequest)-[:HAS_CODE_CHANGE]->(n)
            RETURN pr.title AS pr_title LIMIT 1
        }}
        CALL (n) {{
            MATCH (c:CodeChange)
            WHERE c.source_instance = n.source_instance AND c.repository = n.repository
              AND c.pr_number = n.pr_number AND c.file_path = n.file_path
            RETURN max(c.version_number) AS latest_version
        }}
        {_MENTIONS}
        RETURN elementId(n) AS id, {_PROPS}, pr_title, latest_version, mentions
    """,
    "Topic": f"""
        MATCH (n:Topic)
        CALL (n) {{
            OPTIONAL MATCH (i:Issue)-[:ABOUT_TOPIC]->(n)
            RETURN collect(DISTINCT i.issue_key) AS issues
        }}
        CALL (n) {{
            OPTIONAL MATCH (e:Event)-[:EVENT_OF_TOPIC]->(n)
            RETURN count(e) AS event_count, min(e.occurred_at) AS first_event_at, max(e.occurred_at) AS last_event_at
        }}
        {_EXPERTS}
        RETURN elementId(n) AS id, {_PROPS}, issues, event_count, first_event_at, last_event_at, experts
    """,
    "Event": f"""
        MATCH (n:Event)
        CALL (n) {{
            OPTIONAL MATCH (n)-[:EVENT_OF_TOPIC]->(t:Topic)
            RETURN t.name AS topic_name LIMIT 1
        }}
        CALL (n) {{
            OPTIONAL MATCH (p:Person)-[:ACTED_IN_EVENT]->(n)
            RETURN collect(DISTINCT p.name) AS actors
        }}
        CALL (n) {{
            OPTIONAL MATCH (c:Event)-[r]->(n) WHERE type(r) IN $causal_types
            RETURN collect({{name: c.name, explanation: r.explanation}}) AS caused_by
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[r]->(e:Event) WHERE type(r) IN $causal_types
            RETURN collect({{name: e.name, explanation: r.explanation}}) AS led_to
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[r:HAS_ROOT_CAUSE]->(rc:RootCause)
            RETURN collect({{name: rc.name, explanation: r.explanation}}) AS root_causes
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[r:AFFECTED_COMPONENT]->(c:Component)
            RETURN collect({{name: c.name, explanation: r.explanation}}) AS components
        }}
        CALL (n) {{
            OPTIONAL MATCH (cc:CodeChange)-[r:CONTRIBUTED_TO]->(n)
            RETURN collect({{name: cc.display_name, version: cc.version_number,
                             type: r.contribution_type, explanation: r.explanation}}) AS code
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[:EVIDENCED_BY]->(s)
            RETURN collect({_EVIDENCE_PROPS}) AS evidence
        }}
        RETURN elementId(n) AS id, {_PROPS}, topic_name, actors, caused_by, led_to,
               root_causes, components, code, evidence
    """,
    "Component": f"""
        MATCH (n:Component)
        CALL (n) {{
            OPTIONAL MATCH (n)-[:IMPLEMENTED_IN]->(f:File)
            RETURN collect(DISTINCT f.path) AS files
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[r:DEPENDS_ON]->(o:Component)
            RETURN collect({{name: o.name, type: r.dependency_type, explanation: r.explanation}}) AS depends_on
        }}
        CALL (n) {{
            OPTIONAL MATCH (o:Component)-[r:DEPENDS_ON]->(n)
            RETURN collect({{name: o.name, type: r.dependency_type, explanation: r.explanation}}) AS used_by
        }}
        CALL (n) {{
            OPTIONAL MATCH (e:Event)-[:AFFECTED_COMPONENT]->(n)
            RETURN collect(DISTINCT {{name: e.name, at: e.occurred_at}}) AS events
        }}
        CALL (n) {{
            OPTIONAL MATCH (rc:RootCause)-[:ROOT_CAUSE_IN_COMPONENT]->(n)
            RETURN collect(DISTINCT rc.name) AS root_causes
        }}
        {_EXPERTS}
        RETURN elementId(n) AS id, {_PROPS}, files, depends_on, used_by, events, root_causes, experts
    """,
    "RootCause": f"""
        MATCH (n:RootCause)
        CALL (n) {{
            OPTIONAL MATCH (e:Event)-[r:HAS_ROOT_CAUSE]->(n)
            RETURN collect({{name: e.name, explanation: r.explanation}}) AS explains
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[:ROOT_CAUSE_IN_COMPONENT]->(c:Component)
            RETURN collect(DISTINCT c.name) AS components
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[:ROOT_CAUSE_EVIDENCED_BY]->(s)
            RETURN collect({_EVIDENCE_PROPS}) AS evidence
        }}
        RETURN elementId(n) AS id, {_PROPS}, explains, components, evidence
    """,
    "Person": f"""
        MATCH (n:Person)
        WHERE {ELIGIBLE_PERSON_WHERE}
        CALL (n) {{
            OPTIONAL MATCH (n)-[:HAS_EXPERTISE]->(x:Expertise)-[:EXPERTISE_IN]->(s)
            RETURN collect({{subject: coalesce(s.name, s.display_name), subject_label: labels(s)[0],
                             share: x.share, rank: x.rank}}) AS expertise
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[r:WORKS_WITH]-(o:Person)
            RETURN collect({{name: o.name, weight: r.weight, types: r.work_item_types}}) AS works_with
        }}
        CALL (n) {{
            OPTIONAL MATCH (n)-[:MEMBER_OF_COMMUNITY]->(:Community)<-[:MEMBER_OF_COMMUNITY]-(o:Person)
            WHERE o <> n
            RETURN collect(DISTINCT o.name) AS community_members
        }}
        RETURN elementId(n) AS id, {_PROPS}, expertise, works_with, community_members,
               COUNT {{ (n)-[:AUTHORED_PR]->() }} AS prs_authored,
               COUNT {{ (n)-[:WROTE_PR_REVIEW]->() }} AS pr_reviews,
               COUNT {{ (n)-[:SENT_SLACK_MESSAGE]->() }} AS slack_messages,
               COUNT {{ (n)-[:SENT_MAIL]->() }} AS mails,
               COUNT {{ (n)-[:WROTE_ISSUE_COMMENT]->() }} AS issue_comments,
               COUNT {{ (n)-[:AUTHORED_DOCUMENT_VERSION]->() }} AS document_versions,
               COUNT {{ (n)-[:SPOKE_TEAMS_TRANSCRIPT_SEGMENT]->() }} AS segments,
               COUNT {{ (n)-[:ACTED_IN_EVENT]->() }} AS events
    """,
    "Community": f"""
        MATCH (n:Community)
        CALL (n) {{
            OPTIONAL MATCH (p:Person)-[:MEMBER_OF_COMMUNITY]->(n)
            RETURN collect(DISTINCT p.name) AS members
        }}
        CALL (n) {{
            OPTIONAL MATCH (a:Person)-[:MEMBER_OF_COMMUNITY]->(n)<-[:MEMBER_OF_COMMUNITY]-(b:Person),
                           (a)-[r:WORKS_WITH]->(b)
            RETURN collect(r.work_item_types) AS work_item_types, collect(r.shared_work_items) AS work_items
        }}
        RETURN elementId(n) AS id, {_PROPS}, members, work_item_types, work_items
    """,
}


class LabelConfig:
    def __init__(self, label: str, layer: str, text_fn: Callable[[dict], str]):
        self.label = label
        self.layer = layer
        self.text_fn = text_fn
        self.group_fn = GROUPS[label]
        self.query = QUERIES[label]


# Order is the pipeline order, so the tab lists the layers top to bottom.
LABELS = [
    LabelConfig("MailMessage", "Import", _mail_message_text),
    LabelConfig("SlackMessage", "Import", _slack_message_text),
    LabelConfig("TeamsTranscriptSegment", "Import", _teams_transcript_segment_text),
    LabelConfig("IssueVersion", "Import", _issue_version_text),
    LabelConfig("IssueComment", "Import", _issue_comment_text),
    LabelConfig("DocumentVersion", "Import", _document_version_text),
    LabelConfig("PullRequest", "Import", _pull_request_text),
    LabelConfig("PullRequestReview", "Import", _pull_request_review_text),
    LabelConfig("CodeChange", "Import", _code_change_text),
    LabelConfig("Topic", "Knowledge", _topic_text),
    LabelConfig("Event", "Knowledge", _event_text),
    LabelConfig("Component", "Architecture", _component_text),
    LabelConfig("RootCause", "Root cause & impact", _root_cause_text),
    LabelConfig("Person", "Expertise & collaboration", _person_text),
    LabelConfig("Community", "Graph algorithms", _community_text),
]
LABELS_BY_NAME = {cfg.label: cfg for cfg in LABELS}


class PrerequisiteError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Chunking, hashing, batching.
# ---------------------------------------------------------------------------

def split_text(text: str) -> list[str]:
    """One part for a text that fits, else overlapping parts under a repeated first line."""
    if len(text) <= MAX_TEXT_CHARS:
        return [text]
    header, _, rest = text.partition("\n")
    windows = []
    start = 0
    while start < len(rest):
        end = min(len(rest), start + CHUNK_BODY_CHARS)
        if end < len(rest):
            # Cut at the last line break or space in the window's final fifth, when there is one.
            floor = start + CHUNK_BODY_CHARS * 4 // 5
            cut = max(rest.rfind("\n", floor, end), rest.rfind(" ", floor, end))
            if cut > start:
                end = cut
        windows.append(rest[start:end].strip())
        if end >= len(rest):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    total = len(windows)
    return [f"{header}\n(Part {index} of {total})\n{window}" for index, window in enumerate(windows, start=1)]


def _source_hash(text: str, group: str, is_latest: bool) -> str:
    material = f"{text}\n\x1f{group}\n\x1f{is_latest}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _estimate_tokens(chars: int) -> int:
    return -(-chars // CHARS_PER_TOKEN_ESTIMATE)


def _batches(pieces: list[dict]):
    """Groups pieces by both count and total text length."""
    batch, chars = [], 0
    for piece in pieces:
        size = len(piece["text"])
        if batch and (len(batch) >= BATCH_MAX_INPUTS or chars + size > BATCH_MAX_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(piece)
        chars += size
    if batch:
        yield batch


# ---------------------------------------------------------------------------
# Index setup. Idempotent, safe to run at the start of every pass.
# ---------------------------------------------------------------------------

def drop_legacy_indexes(tx):
    for name in LEGACY_VECTOR_INDEXES:
        tx.run(f"DROP INDEX {name} IF EXISTS")


def ensure_vector_index(tx):
    tx.run(f"""
        CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS
        FOR (n:{SEARCHABLE_LABEL}) ON (n.embedding)
        OPTIONS {{indexConfig: {{
          `vector.dimensions`: $dimensions,
          `vector.similarity_function`: $similarity
        }}}}
    """, {"dimensions": EMBEDDING_DIMENSIONS, "similarity": EMBEDDING_SIMILARITY})


def ensure_fulltext_indexes(tx):
    tx.run(f"""
        CREATE FULLTEXT INDEX {FULLTEXT_INDEX_NAME} IF NOT EXISTS
        FOR (n:{SEARCHABLE_LABEL}) ON EACH [n.embedding_text]
    """)
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


# The four indexes this layer owns, as they are defined when created. Used to
# list an index that does not exist yet (before the first run) with its intended shape.
EXPECTED_INDEXES = [
    {"name": VECTOR_INDEX_NAME, "type": "VECTOR", "labels": [SEARCHABLE_LABEL], "properties": ["embedding"]},
    {"name": FULLTEXT_INDEX_NAME, "type": "FULLTEXT", "labels": [SEARCHABLE_LABEL], "properties": ["embedding_text"]},
    {"name": "entity_lookup", "type": "FULLTEXT", "labels": FULLTEXT_ENTITY_LABELS,
     "properties": FULLTEXT_ENTITY_PROPERTIES},
    {"name": "issue_key_lookup", "type": "FULLTEXT", "labels": ["Issue"], "properties": ["issue_key"]},
]
INDEX_NOT_CREATED = "NOT CREATED"


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------

def read_label_rows(tx, cfg: LabelConfig):
    return [dict(row) for row in tx.run(cfg.query, {
        "reference_extractor": REFERENCE_EXTRACTOR_NAME,
        # Filtered by name rather than written as `[:CAUSED|CROSS_TOPIC_CAUSED]`,
        # so Neo4j does not warn on every read while no cross-topic link exists.
        "causal_types": ["CAUSED", "CROSS_TOPIC_CAUSED"],
    })]


def read_label_total(tx, label):
    return tx.run(f"MATCH (n:`{label}`) RETURN count(n) AS total").single()["total"]


def read_embedding_models_in_use(tx):
    # By property, not by `:Searchable`, so Neo4j does not warn before the label exists.
    rows = tx.run("""
        MATCH (n)
        WHERE n.embedding_model IS NOT NULL
        RETURN DISTINCT n.embedding_model AS model
    """)
    return sorted(row["model"] for row in rows)


def read_chunk_count(tx):
    return tx.run("""
        MATCH (n) WHERE n.embedding IS NOT NULL AND n.generated_by STARTS WITH $chunk_prefix
        RETURN count(n) AS chunks
    """, {"chunk_prefix": CHUNK_GENERATED_BY_PREFIX}).single()["chunks"]


def read_indexes(tx):
    names = [index["name"] for index in EXPECTED_INDEXES]
    rows = tx.run("""
        SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state
        WHERE name IN $names
        RETURN name, type, labelsOrTypes AS labels, properties, state
    """, {"names": names})
    by_name = {row["name"]: dict(row) for row in rows}
    return [by_name.get(index["name"], {**index, "state": INDEX_NOT_CREATED}) for index in EXPECTED_INDEXES]


def read_excluded_persons(tx):
    return [dict(row) for row in tx.run("""
        MATCH (p:Person)
        WHERE p.actor_type = 'mailbox' OR p.identity_ambiguous = true
        RETURN p.name AS person_name, p.person_key AS person_key,
               CASE WHEN p.actor_type = 'mailbox' THEN 'mailbox' ELSE 'ambiguous identity' END AS reason
        ORDER BY p.person_key
    """)]


def _node_status(props: dict, source_hash: str, parts: int) -> str:
    if props.get("embedding_model") is None or props.get("embedding_source_hash") is None:
        return "missing"
    current = (
        props.get("embedding_source_hash") == source_hash
        and props.get("embedding_model") == EMBEDDING_MODEL
        and props.get("embedding_version") == EMBEDDING_VERSION
        # The same text split differently (a changed chunk size) must be re-split and re-embedded.
        and props.get("embedding_parts") == parts
    )
    return "current" if current else "outdated"


def _assemble(tx, labels: Optional[list[str]] = None):
    """Reads every eligible node of the given labels and assembles its text. Only reads."""
    configs = [cfg for cfg in LABELS if labels is None or cfg.label in labels]
    assembled = []
    totals = {}
    for cfg in configs:
        totals[cfg.label] = read_label_total(tx, cfg.label)
        for row in read_label_rows(tx, cfg):
            props = row["props"]
            text = cfg.text_fn(row)
            group, is_latest = cfg.group_fn(row)
            source_hash = _source_hash(text, group, is_latest) if text else None
            parts = split_text(text) if text else []
            assembled.append({
                "id": row["id"],
                "layer": cfg.layer,
                "label": cfg.label,
                "display_name": props.get("display_name") or props.get("name") or props.get("community_id"),
                "text": text,
                "parts": parts,
                "group": group,
                "is_latest": is_latest,
                "hash": source_hash,
                "status": _node_status(props, source_hash, len(parts)) if text else "empty",
                "embedded_at": props.get("embedded_at"),
            })
    # Same order as the stored path's ORDER BY: pipeline label order, then name, then element id.
    label_order = {cfg.label: index for index, cfg in enumerate(LABELS)}
    assembled.sort(key=lambda node: (label_order[node["label"]], node["display_name"] or "", node["id"]))
    return assembled, totals


def _per_label(assembled, totals):
    rows = []
    for cfg in LABELS:
        if cfg.label not in totals:
            continue
        nodes = [node for node in assembled if node["label"] == cfg.label]
        rows.append({
            "layer": cfg.layer,
            "label": cfg.label,
            "total": totals[cfg.label],
            "eligible": len(nodes),
            "embedded": sum(1 for node in nodes if node["status"] == "current"),
            "outdated": sum(1 for node in nodes if node["status"] == "outdated"),
            "missing": sum(1 for node in nodes if node["status"] in ("missing", "empty")),
        })
    return rows


def _run_plan(assembled, force: bool) -> list[dict]:
    """The nodes a run would embed: all with text when forced, else the outdated and missing ones."""
    return [
        node for node in assembled
        if node["text"] and (force or node["status"] != "current")
    ]


def _plan_summary(plan: list[dict]) -> dict:
    chars = sum(len(part) for node in plan for part in node["parts"])
    return {
        "nodes": len(plan),
        "parts": sum(len(node["parts"]) for node in plan),
        "chunks": sum(len(node["parts"]) - 1 for node in plan),
        "characters": chars,
        "estimated_tokens": _estimate_tokens(chars),
    }


# ---------------------------------------------------------------------------
# Stored path. A text can only change when an upstream layer is rebuilt, which
# the pipeline staleness already tracks. So when the layer is not stale, the
# last run had no failures and the configuration it ran with is the current
# one, every node's stored state is exact, and the tab reads it with plain
# counts instead of assembling every text. Otherwise the texts are assembled
# (the assembled path above), as the run itself always does.
# ---------------------------------------------------------------------------

def config_fingerprint() -> str:
    """Everything besides the graph that decides the texts and how they are split and embedded."""
    settings = {
        name: value for name, value in globals().items()
        if name.startswith("MAX_") or name in (
            "EMBEDDING_VERSION", "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS", "CHUNK_BODY_CHARS",
            "CHUNK_OVERLAP_CHARS", "QUOTE_CHARS", "BRIDGE_BETWEENNESS",
        )
    }
    return hashlib.sha256(repr(sorted(settings.items())).encode("utf-8")).hexdigest()


def read_run_markers(tx) -> dict:
    """This layer's own PipelineState fields (read as a map, so a missing field does not warn)."""
    row = tx.run(
        f"MATCH (s:{PIPELINE_STATE_LABEL} {{id: $id}}) RETURN properties(s) AS props", {"id": PIPELINE_STATE_ID},
    ).single()
    props = row["props"] if row else {}
    return {
        "failures": props.get("last_embedding_failures"),
        "config": props.get("embedding_config"),
    }


def _stored_state_is_exact(stale: bool, markers: dict) -> bool:
    return not stale and markers.get("failures") == 0 and markers.get("config") == config_fingerprint()


def _eligible_where(cfg: LabelConfig) -> str:
    return f"WHERE {ELIGIBLE_PERSON_WHERE}" if cfg.label == "Person" else ""


_CURRENT = "(n.embedding_version = $version AND n.embedding_model = $model)"
_STORED_STATUS_WHERE = {
    "current": _CURRENT,
    "outdated": f"(n.embedding_model IS NOT NULL AND NOT {_CURRENT})",
    "missing": "n.embedding_model IS NULL",
}
_STORED_PARAMS = {"version": EMBEDDING_VERSION, "model": EMBEDDING_MODEL}


def _stored_per_label(tx) -> list[dict]:
    rows = []
    for cfg in LABELS:
        row = tx.run(f"""
            MATCH (n:`{cfg.label}`) {_eligible_where(cfg)}
            RETURN count(n) AS eligible,
                   count(CASE WHEN {_STORED_STATUS_WHERE["current"]} THEN 1 END) AS embedded,
                   count(CASE WHEN {_STORED_STATUS_WHERE["outdated"]} THEN 1 END) AS outdated,
                   count(CASE WHEN {_STORED_STATUS_WHERE["missing"]} THEN 1 END) AS missing
        """, {"version": EMBEDDING_VERSION, "model": EMBEDDING_MODEL}).single()
        rows.append({
            "layer": cfg.layer,
            "label": cfg.label,
            "total": read_label_total(tx, cfg.label),
            "eligible": row["eligible"],
            "embedded": row["embedded"],
            "outdated": row["outdated"],
            "missing": row["missing"],
        })
    return rows


def _stored_page(tx, labels: list[str], status: Optional[str], offset: int, limit: int) -> dict:
    """One page read from stored properties, walking the labels in pipeline order."""
    configs = [cfg for cfg in LABELS if cfg.label in labels]
    conditions = [_STORED_STATUS_WHERE[status]] if status in _STORED_STATUS_WHERE else []

    def _where(cfg):
        parts = ([ELIGIBLE_PERSON_WHERE] if cfg.label == "Person" else []) + conditions
        return f"WHERE {' AND '.join(parts)}" if parts else ""

    counts = [
        tx.run(f"MATCH (n:`{cfg.label}`) {_where(cfg)} RETURN count(n) AS c", _STORED_PARAMS).single()["c"]
        for cfg in configs
    ]
    total = sum(counts)
    nodes = []
    skip = offset
    for cfg, count in zip(configs, counts):
        if len(nodes) >= limit:
            break
        if skip >= count:
            skip -= count
            continue
        rows = tx.run(f"""
            MATCH (n:`{cfg.label}`) {_where(cfg)}
            WITH n, coalesce(n.display_name, n.name, n.community_id) AS display_name
            RETURN display_name, n.embedding_text AS text, n.embedding_group AS grp,
                   n.embedding_is_latest AS is_latest, n.embedding_parts AS parts, n.embedded_at AS embedded_at,
                   CASE WHEN {_STORED_STATUS_WHERE["current"]} THEN 'current'
                        WHEN n.embedding_model IS NULL THEN 'missing'
                        ELSE 'outdated' END AS status
            ORDER BY coalesce(display_name, ''), elementId(n)
            SKIP $skip LIMIT $limit
        """, {**_STORED_PARAMS, "skip": skip, "limit": limit - len(nodes)})
        for row in rows:
            nodes.append({
                "layer": cfg.layer,
                "label": cfg.label,
                "display_name": row["display_name"],
                "text": row["text"] or "",
                "group": row["grp"] or "",
                "is_latest": bool(row["is_latest"]) if row["is_latest"] is not None else True,
                "parts": row["parts"] or 0,
                "status": row["status"],
                "embedded_at": row["embedded_at"],
            })
        skip = 0
    return {"total": total, "nodes": nodes}


def load_embedding_state(session):
    """Summary for the tab: configuration, coverage per label, indexes, pipeline. No node texts."""
    def _read(tx):
        pipeline = _read_full_pipeline_state(tx)
        stale = compute_staleness(pipeline)["embeddings"]["stale"]
        stored = _stored_state_is_exact(stale, read_run_markers(tx))
        if stored:
            per_label = _stored_per_label(tx)
        else:
            assembled, totals = _assemble(tx)
            per_label = _per_label(assembled, totals)
        return {
            "per_label": per_label,
            "counted_from": "stored" if stored else "assembled",
            "models_in_use": read_embedding_models_in_use(tx),
            "chunks": read_chunk_count(tx),
            "indexes": read_indexes(tx),
            "excluded_persons": read_excluded_persons(tx),
            "pipeline": pipeline,
        }

    result = session.execute_read(_read)
    pipeline = result["pipeline"]
    staleness = compute_staleness(pipeline)["embeddings"]
    per_label = result["per_label"]
    counts = {
        "eligible": sum(row["eligible"] for row in per_label),
        "embedded": sum(row["embedded"] for row in per_label),
        "outdated": sum(row["outdated"] for row in per_label),
        "missing": sum(row["missing"] for row in per_label),
        "chunks": result["chunks"],
    }
    return {
        "provider": EMBEDDING_PROVIDER,
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "similarity": EMBEDDING_SIMILARITY,
        "version": EMBEDDING_VERSION,
        "models_in_use": result["models_in_use"],
        "counts": counts,
        "counted_from": result["counted_from"],
        "per_label": per_label,
        "indexes": result["indexes"],
        "excluded_persons": result["excluded_persons"],
        **pipeline,
        "needs_rerun": staleness["stale"],
        "stale_reasons": staleness["reasons"],
    }


def embedding_state_payload(session):
    return load_embedding_state(session)


def embedding_nodes_page(session, layer=None, label=None, status=None, offset=0, limit=50):
    """One page of the embedded-texts table, filtered by layer, label and status."""
    labels = [cfg.label for cfg in LABELS if (not layer or cfg.layer == layer) and (not label or cfg.label == label)]
    offset = max(0, int(offset))
    limit = max(1, min(500, int(limit)))

    def _read(tx):
        stale = compute_staleness(_read_full_pipeline_state(tx))["embeddings"]["stale"]
        if _stored_state_is_exact(stale, read_run_markers(tx)):
            return {**_stored_page(tx, labels, status, offset, limit), "counted_from": "stored"}

        assembled, _totals = _assemble(tx, labels)
        if status:
            wanted = {"missing", "empty"} if status == "missing" else {status}
            assembled = [node for node in assembled if node["status"] in wanted]
        return {
            "total": len(assembled),
            "counted_from": "assembled",
            "nodes": [
                {
                    "layer": node["layer"],
                    "label": node["label"],
                    "display_name": node["display_name"],
                    # The node's own vector text: the whole text, or part 1 when it is chunked.
                    "text": node["parts"][0] if node["parts"] else (node["text"] or ""),
                    "group": node["group"],
                    "is_latest": node["is_latest"],
                    "parts": len(node["parts"]),
                    "status": node["status"],
                    "embedded_at": node["embedded_at"],
                }
                for node in assembled[offset:offset + limit]
            ],
        }

    return {**session.execute_read(_read), "offset": offset, "limit": limit}


def embedding_preview(session, force=False):
    """What the next run would do, without calling OpenAI or writing anything."""
    assembled, _totals = session.execute_read(_assemble)
    plan = _run_plan(assembled, force)
    per_label = []
    for cfg in LABELS:
        nodes = [node for node in plan if node["label"] == cfg.label]
        if nodes:
            per_label.append({"label": cfg.label, **_plan_summary(nodes)})
    return {"forced": force, **_plan_summary(plan), "per_label": per_label}


# ---------------------------------------------------------------------------
# Writes. On other layers' nodes: only the embedding properties and the
# Searchable label. Own nodes: EmbeddingChunk + CHUNK_OF.
# ---------------------------------------------------------------------------

def write_node_group(tx, node: dict, vectors: list, embedded_at: str):
    """Writes one node's vector and replaces its chunks, in one transaction."""
    # Chunks are found by property, so Neo4j does not warn before the label exists.
    tx.run("""
        MATCH (chunk)-->(src)
        WHERE elementId(src) = $id AND chunk.generated_by STARTS WITH $chunk_prefix
        DETACH DELETE chunk
    """, {"id": node["id"], "chunk_prefix": CHUNK_GENERATED_BY_PREFIX})
    tx.run(f"""
        MATCH (n) WHERE elementId(n) = $id
        CALL db.create.setNodeVectorProperty(n, 'embedding', $vector)
        SET n:{SEARCHABLE_LABEL},
            n.embedding_text = $text,
            n.embedding_model = $model,
            n.embedding_source_hash = $hash,
            n.embedding_version = $version,
            n.embedding_group = $group,
            n.embedding_is_latest = $is_latest,
            n.embedding_parts = $parts,
            n.embedded_at = $embedded_at
    """, {
        "id": node["id"], "vector": vectors[0], "text": node["parts"][0], "model": EMBEDDING_MODEL,
        "hash": node["hash"], "version": EMBEDDING_VERSION, "group": node["group"],
        "is_latest": node["is_latest"], "parts": len(node["parts"]), "embedded_at": embedded_at,
    })
    if len(node["parts"]) > 1:
        chunks = [
            {"index": index, "text": text, "vector": vector,
             "display_name": f'{node["display_name"]} (part {index} of {len(node["parts"])})'}
            for index, (text, vector) in enumerate(zip(node["parts"][1:], vectors[1:]), start=2)
        ]
        tx.run(f"""
            MATCH (src) WHERE elementId(src) = $id
            UNWIND $chunks AS c
            CREATE (k:{CHUNK_LABEL}:{SEARCHABLE_LABEL} {{
                display_name: c.display_name,
                chunk_of_label: $label,
                chunk_index: c.index,
                chunk_count: $count,
                embedding_text: c.text,
                embedding_model: $model,
                embedding_version: $version,
                embedding_group: $group,
                embedding_is_latest: $is_latest,
                embedded_at: $embedded_at,
                derived: true,
                generated_by: $version,
                generated_at: $embedded_at
            }})
            CREATE (k)-[:{CHUNK_RELATIONSHIP} {{derived: true, generated_by: $version, generated_at: $embedded_at}}]->(src)
            WITH k, c
            CALL db.create.setNodeVectorProperty(k, 'embedding', c.vector)
        """, {
            "id": node["id"], "chunks": chunks, "label": node["label"], "count": len(node["parts"]),
            "model": EMBEDDING_MODEL, "version": EMBEDDING_VERSION, "group": node["group"],
            "is_latest": node["is_latest"], "embedded_at": embedded_at,
        })


def remove_ineligible(tx, eligible_ids):
    """Strips this layer's properties and label from nodes that are no longer eligible,
    and deletes chunks whose source is gone or no longer eligible."""
    removals = ", ".join(f"n.{prop}" for prop in EMBEDDING_PROPERTIES)
    removed = tx.run(f"""
        MATCH (n)
        WHERE (n:{SEARCHABLE_LABEL} OR n.embedding IS NOT NULL)
          AND NOT coalesce(n.generated_by, '') STARTS WITH $chunk_prefix
          AND NOT elementId(n) IN $ids
        REMOVE n:{SEARCHABLE_LABEL}, {removals}
        RETURN count(n) AS removed
    """, {"ids": eligible_ids, "chunk_prefix": CHUNK_GENERATED_BY_PREFIX}).single()["removed"]
    # Chunk nodes are found by property, so Neo4j does not warn before the label exists.
    removed_chunks = tx.run("""
        MATCH (k)
        WHERE k.generated_by STARTS WITH $chunk_prefix
        OPTIONAL MATCH (k)-->(src)
        WITH k, src
        WHERE src IS NULL OR NOT elementId(src) IN $ids
        DETACH DELETE k
        RETURN count(k) AS removed
    """, {"ids": eligible_ids, "chunk_prefix": CHUNK_GENERATED_BY_PREFIX}).single()["removed"]
    return removed, removed_chunks


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def run_embedding_pass(session, force=False):
    pipeline = session.execute_read(_read_full_pipeline_state)
    if not pipeline.get("last_algorithms_run_at"):
        raise PrerequisiteError("Run Graph algorithms first.")

    client = require_openai_client().with_options(max_retries=API_MAX_RETRIES, timeout=API_TIMEOUT_SECONDS)
    embedded_at = now_iso()

    session.execute_write(drop_legacy_indexes)
    session.execute_write(ensure_vector_index)
    session.execute_write(ensure_fulltext_indexes)

    assembled, _totals = session.execute_read(_assemble)
    plan = _run_plan(assembled, force)
    planned_ids = {node["id"] for node in plan}

    per_label_run = {cfg.label: {"label": cfg.label, "embedded": 0, "chunks": 0, "skipped_unchanged": 0,
                                 "skipped_empty": 0, "failed": 0} for cfg in LABELS}
    for node in assembled:
        if not node["text"]:
            per_label_run[node["label"]]["skipped_empty"] += 1
        elif node["id"] not in planned_ids:
            per_label_run[node["label"]]["skipped_unchanged"] += 1

    # Every part of every planned node, in node order. A node is written as soon
    # as all of its parts have a vector; a node with a failed part is not written.
    pieces = [
        {"node_index": node_index, "part_index": part_index, "text": text}
        for node_index, node in enumerate(plan)
        for part_index, text in enumerate(node["parts"])
    ]
    vectors: dict[int, dict[int, list]] = {}
    failed_nodes: set[int] = set()
    written_nodes: set[int] = set()
    failures = []
    total_tokens = 0
    has_token_usage = False

    def _fail(node_index, error):
        if node_index in failed_nodes or node_index in written_nodes:
            return
        failed_nodes.add(node_index)
        node = plan[node_index]
        per_label_run[node["label"]]["failed"] += 1
        failures.append({"label": node["label"], "key": node["display_name"], "error": str(error)})

    def _write_completed():
        for node_index in sorted(vectors):
            node = plan[node_index]
            if node_index in failed_nodes or len(vectors[node_index]) < len(node["parts"]):
                continue
            ordered = [vectors[node_index][part] for part in range(len(node["parts"]))]
            try:
                session.execute_write(write_node_group, node, ordered, embedded_at)
                written_nodes.add(node_index)
                per_label_run[node["label"]]["embedded"] += 1
                per_label_run[node["label"]]["chunks"] += len(node["parts"]) - 1
            except Exception as error:
                _fail(node_index, error)
        for node_index in list(vectors):
            if node_index in written_nodes or node_index in failed_nodes:
                del vectors[node_index]

    for batch in _batches(pieces):
        batch = [piece for piece in batch if piece["node_index"] not in failed_nodes]
        if not batch:
            continue
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                dimensions=EMBEDDING_DIMENSIONS,
                input=[piece["text"] for piece in batch],
            )
        except Exception as error:
            for piece in batch:
                _fail(piece["node_index"], error)
            _write_completed()
            continue

        usage = getattr(response, "usage", None)
        if usage is not None:
            has_token_usage = True
            total_tokens += usage.total_tokens

        for piece, item in zip(batch, response.data):
            vectors.setdefault(piece["node_index"], {})[piece["part_index"]] = item.embedding
        _write_completed()

    eligible_ids = [node["id"] for node in assembled]
    removed, removed_chunks = session.execute_write(remove_ineligible, eligible_ids)

    session.execute_write(touch_pipeline_state, "last_embedding_at", embedded_at)
    # Read by the stored path: its counts are exact only after a run without failures
    # that used the current configuration.
    session.execute_write(touch_pipeline_state, "last_embedding_failures", len(failures))
    session.execute_write(touch_pipeline_state, "embedding_config", config_fingerprint())

    run = list(per_label_run.values())
    state = load_embedding_state(session)
    state.update({
        "run_at": embedded_at,
        "forced": force,
        "per_label_run": run,
        "embedded_now": sum(row["embedded"] for row in run),
        "chunks_now": sum(row["chunks"] for row in run),
        "skipped_unchanged": sum(row["skipped_unchanged"] for row in run),
        "skipped_empty": sum(row["skipped_empty"] for row in run),
        "removed": removed,
        "removed_chunks": removed_chunks,
        "failed": len(failures),
        "failures": failures,
        "token_usage": {"total_tokens": total_tokens} if has_token_usage else None,
    })
    return state


def build_embeddings(session, force=False):
    return run_embedding_pass(session, force=force)
