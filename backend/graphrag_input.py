"""Microsoft GraphRAG input: the source records exported from PostgreSQL as documents for GraphRAG.

GraphRAG is a separate RAG system next to the graph layers. It reads plain documents and builds its own graph from
them, so this module turns the six sources into one JSON Lines file, one document per line (`id`, `title`, `text`,
plus metadata), which GraphRAG's `jsonl` input reader reads directly.

The records are interpreted as the Neo4j import interprets them (`docs/SQL_DATA_HANDOFF.md`): the same objects,
identifiers and person fields; every version of Slack messages, issues, documents and pull requests; only the latest
version of an issue comment and a review entry. What differs is the size of a document: GraphRAG's model reads text
chunks and sees only what the chunk says, so a parent and its children become one document, the way the Knowledge
layer reads a whole meeting: a meeting with all its segments, an issue with its versions and comments, a pull request
with its versions, code changes and reviews, a document with all its versions, a mail or Slack thread with its
replies. Authors, times, versions and identifiers are written into the text, since GraphRAG's model sees nothing else.

This module reads PostgreSQL only, in a read-only transaction, and writes only the export file. It never touches
Neo4j.
"""

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRAPHRAG_PROJECT_DIR = PROJECT_ROOT / "graphrag_project"
INPUT_FILE = GRAPHRAG_PROJECT_DIR / "input" / "documents.jsonl"

SOURCE_ORDER = ["mail", "slack", "teams", "issues", "documents", "pull_requests"]
SOURCE_LABELS = {
    "mail": "Mail thread",
    "slack": "Slack thread",
    "teams": "Teams meeting",
    "issues": "Issue",
    "documents": "Document",
    "pull_requests": "Pull request",
}


# ---------------------------------------------------------------------------
# Reads (one read-only transaction).
# ---------------------------------------------------------------------------

QUERIES = {
    "mail": "SELECT * FROM mail_messages ORDER BY sent_at, message_id",
    "slack": "SELECT * FROM slack_messages ORDER BY sent_at, message_id, version_number",
    "meetings": "SELECT * FROM teams_meetings ORDER BY started_at, meeting_id",
    "segments": "SELECT * FROM teams_transcript_segments ORDER BY meeting_id, sequence_number",
    "issues": "SELECT * FROM issues ORDER BY created_at, issue_id",
    "issue_versions": "SELECT * FROM issue_versions ORDER BY issue_id, version_number",
    # Only the latest version of each comment, as the import does.
    "issue_comments": """
        SELECT DISTINCT ON (source_instance, comment_id) *
        FROM issue_comments
        ORDER BY source_instance, comment_id, version_number DESC
    """,
    "document_versions": "SELECT * FROM document_versions ORDER BY document_id, version_number",
    "pr_versions": "SELECT * FROM pr_versions ORDER BY repository, pr_number, version_number",
    # Only the latest version of each review entry, as the import does.
    "pr_reviews": """
        SELECT DISTINCT ON (source_instance, repository, pr_number, source_id) *
        FROM pr_reviews
        ORDER BY source_instance, repository, pr_number, source_id, version_number DESC
    """,
}


def read_sources(database_url: str) -> dict[str, list[dict]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cursor:
            rows = {}
            for name, query in QUERIES.items():
                cursor.execute(query)
                rows[name] = cursor.fetchall()
    return rows


# ---------------------------------------------------------------------------
# Text helpers.
# ---------------------------------------------------------------------------

def iso(value) -> str:
    return value.isoformat() if value is not None else ""


def person(name: str | None, address: str | None = None) -> str:
    if name and address:
        return f"{name} <{address}>"
    return name or address or "(unknown)"


def as_list(value) -> list:
    """JSONB arrays arrive as lists; a JSON string inside is decoded, anything else becomes empty."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def document(doc_id: str, source: str, title: str, lines: list[str], source_ids: list[str], times: list) -> dict:
    dated = sorted(value for value in times if value is not None)
    return {
        "id": doc_id,
        "title": title,
        "text": "\n".join(lines).strip() + "\n",
        "source": source,
        "source_ids": source_ids,
        "first_at": iso(dated[0]) if dated else "",
        "last_at": iso(dated[-1]) if dated else "",
    }


# ---------------------------------------------------------------------------
# One builder per source.
# ---------------------------------------------------------------------------

def mail_documents(rows: list[dict]) -> list[dict]:
    """One document per mail thread: the first mail and every reply to it, following `in_reply_to_id`."""
    by_id = {(row["source_instance"], row["message_id"]): row for row in rows}

    def thread_root(row: dict) -> tuple[str, str]:
        key = (row["source_instance"], row["message_id"])
        seen = {key}
        while by_id[key]["in_reply_to_id"]:
            parent = (key[0], by_id[key]["in_reply_to_id"])
            if parent not in by_id or parent in seen:
                break
            seen.add(parent)
            key = parent
        return key

    threads: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        threads.setdefault(thread_root(row), []).append(row)

    documents = []
    for (instance, root_id), messages in threads.items():
        root = by_id[(instance, root_id)]
        lines = [f"Mail thread: {root['subject'] or '(no subject)'}", f"Source: mail ({instance})", ""]
        for message in messages:
            recipients = as_list(message["recipients"])
            lines.append(f"[{message['message_id']}] {iso(message['sent_at'])}")
            lines.append(f"From: {person(message['sender_name'], message['sender_address'])}")
            for kind in ("to", "cc", "bcc"):
                names = [person(r.get("name"), r.get("address")) for r in recipients if (r.get("type") or "to") == kind]
                if names:
                    lines.append(f"{kind.capitalize()}: {'; '.join(names)}")
            lines.append(f"Subject: {message['subject'] or '(no subject)'}")
            if message["in_reply_to_id"]:
                lines.append(f"In reply to: {message['in_reply_to_id']}")
            lines += ["", message["body"], ""]
        documents.append(document(
            f"mail:{instance}:{root_id}", "mail", f"Mail thread: {root['subject'] or root_id}", lines,
            [m["message_id"] for m in messages], [m["sent_at"] for m in messages],
        ))
    return documents


def slack_documents(rows: list[dict]) -> list[dict]:
    """One document per Slack thread: the root message and its replies, every version of each message."""
    threads: dict[tuple, list[dict]] = {}
    for row in rows:
        root_id = row["thread_root_id"] or row["message_id"]
        key = (row["source_instance"], row["workspace_id"], row["channel_id"], root_id)
        threads.setdefault(key, []).append(row)

    documents = []
    for (instance, workspace, channel, root_id), messages in threads.items():
        channel_name = next((m["channel_name"] for m in messages if m["channel_name"]), channel)
        lines = [f"Slack thread in #{channel_name}", f"Source: Slack ({instance}, workspace {workspace})", ""]
        for message in messages:
            if message["version_number"] == 1:
                header = f"[{message['message_id']}] {iso(message['sent_at'])}"
            else:
                header = (f"[{message['message_id']}, edited to version {message['version_number']}] "
                          f"{iso(message['version_at'])}")
            lines.append(f"{header} {person(message['author_name'], message['author_email'])}:")
            lines += [message["body"], ""]
        root_message = next((m for m in messages if m["message_id"] == root_id), messages[0])
        title_text = " ".join(root_message["body"].split())
        documents.append(document(
            f"slack:{instance}:{channel}:{root_id}", "slack",
            f"Slack #{channel_name}: {title_text[:80]}{'...' if len(title_text) > 80 else ''}", lines,
            sorted({m["message_id"] for m in messages}), [m["version_at"] for m in messages],
        ))
    return documents


def teams_documents(meetings: list[dict], segments: list[dict]) -> list[dict]:
    """One document per meeting: title, time, participants and every segment in order with its speaker."""
    by_meeting: dict[tuple, list[dict]] = {}
    for segment in segments:
        by_meeting.setdefault((segment["source_instance"], segment["meeting_id"]), []).append(segment)

    documents = []
    for meeting in meetings:
        key = (meeting["source_instance"], meeting["meeting_id"])
        meeting_segments = by_meeting.get(key, [])
        participants = [person(p.get("name"), p.get("email") or p.get("address"))
                        for p in as_list(meeting["participants"])]
        lines = [
            f"Teams meeting: {meeting['title']} [{meeting['meeting_id']}]",
            f"Source: Teams ({meeting['source_instance']})",
            f"Started: {iso(meeting['started_at'])}" + (f", ended: {iso(meeting['ended_at'])}"
                                                         if meeting["ended_at"] else ""),
            f"Participants: {'; '.join(participants) or '(none listed)'}",
            "",
            "Transcript:",
        ]
        for segment in meeting_segments:
            lines.append(f"[{segment['segment_id']}] {segment['speaker_name'] or segment['speaker_source_id'] or '(unknown)'}: "
                         f"{segment['body']}")
        documents.append(document(
            f"teams:{meeting['source_instance']}:{meeting['meeting_id']}", "teams",
            f"Teams meeting: {meeting['title']} ({meeting['meeting_id']})", lines,
            [meeting["meeting_id"]] + [s["segment_id"] for s in meeting_segments],
            [meeting["started_at"], meeting["ended_at"]],
        ))
    return documents


def issue_documents(issues: list[dict], versions: list[dict], comments: list[dict]) -> list[dict]:
    """One document per issue: identity, every version in order and every comment (latest version of each)."""
    versions_by_issue: dict[tuple, list[dict]] = {}
    for version in versions:
        versions_by_issue.setdefault((version["source_instance"], version["issue_id"]), []).append(version)
    comments_by_issue: dict[tuple, list[dict]] = {}
    for comment in sorted(comments, key=lambda c: (c["created_at"], c["comment_id"])):
        comments_by_issue.setdefault((comment["source_instance"], comment["issue_id"]), []).append(comment)

    documents = []
    for issue in issues:
        key = (issue["source_instance"], issue["issue_id"])
        issue_versions = versions_by_issue.get(key, [])
        issue_comments = comments_by_issue.get(key, [])
        latest_title = issue_versions[-1]["title"] if issue_versions else ""
        lines = [
            f"Issue {issue['issue_key']} ({issue['issue_id']}): {latest_title}",
            f"Source: issue tracker ({issue['source_instance']})",
            f"Created: {iso(issue['created_at'])} by {issue['creator_name'] or issue['creator_source_id'] or '(unknown)'}",
            "",
        ]
        for version in issue_versions:
            lines.append(f"[{issue['issue_key']} v{version['version_number']}] {iso(version['version_at'])}, changed by "
                         f"{version['changed_by_name'] or version['changed_by_id'] or '(unknown)'}")
            lines.append(f"Type: {version['issue_type']} | Status: {version['status']} | "
                         f"Priority: {version['priority'] or '(none)'} | "
                         f"Assignee: {version['assignee_name'] or version['assignee_source_id'] or '(none)'}")
            lines.append(f"Title: {version['title']}")
            if version["description"]:
                lines.append(f"Description: {version['description']}")
            if version["acceptance_criteria"]:
                lines.append(f"Acceptance criteria: {version['acceptance_criteria']}")
            lines.append("")
        if issue_comments:
            lines.append("Comments:")
            for comment in issue_comments:
                reply = f" (reply to {comment['reply_to_comment_id']})" if comment["reply_to_comment_id"] else ""
                lines.append(f"[{comment['comment_id']}] {iso(comment['created_at'])} "
                             f"{comment['author_name'] or comment['author_source_id'] or '(unknown)'}{reply}:")
                lines += [comment["body"], ""]
        documents.append(document(
            f"issue:{issue['source_instance']}:{issue['issue_key']}", "issues",
            f"{issue['issue_key']}: {latest_title}", lines,
            [issue["issue_key"]] + [f"{issue['issue_key']} v{v['version_number']}" for v in issue_versions]
            + [c["comment_id"] for c in issue_comments],
            [issue["created_at"]] + [v["version_at"] for v in issue_versions] + [c["created_at"] for c in issue_comments],
        ))
    return documents


def document_documents(versions: list[dict]) -> list[dict]:
    """One document per requirement or technical document: every version in order with its change summary."""
    by_document: dict[tuple, list[dict]] = {}
    for version in versions:
        by_document.setdefault((version["source_instance"], version["document_id"]), []).append(version)

    documents = []
    for (instance, document_id), doc_versions in by_document.items():
        latest = doc_versions[-1]
        lines = [
            f"Document {document_id} ({latest['document_type']}): {latest['title']}",
            f"Source: documentation ({instance})",
            f"Created: {iso(latest['created_at'])}",
            "",
        ]
        for version in doc_versions:
            lines.append(f"[{document_id} v{version['version_number']}] {iso(version['version_at'])} by "
                         f"{version['author_name'] or version['author_source_id'] or '(unknown)'}")
            if version["change_summary"]:
                lines.append(f"Change summary: {version['change_summary']}")
            lines.append(f"Title: {version['title']}")
            lines += ["", version["body"], ""]
        documents.append(document(
            f"document:{instance}:{document_id}", "documents", latest["title"], lines,
            [document_id] + [f"{document_id} v{v['version_number']}" for v in doc_versions],
            [latest["created_at"]] + [v["version_at"] for v in doc_versions],
        ))
    return documents


def pull_request_documents(versions: list[dict], reviews: list[dict]) -> list[dict]:
    """One document per pull request: every version with its code changes, then every review entry in order."""
    by_pr: dict[tuple, list[dict]] = {}
    for version in versions:
        by_pr.setdefault((version["source_instance"], version["repository"], version["pr_number"]), []).append(version)
    reviews_by_pr: dict[tuple, list[dict]] = {}
    for review in sorted(reviews, key=lambda r: (r["created_at"], r["source_id"])):
        reviews_by_pr.setdefault((review["source_instance"], review["repository"], review["pr_number"]), []).append(review)

    documents = []
    for (instance, repository, pr_number), pr_versions in by_pr.items():
        reference = f"{repository}#{pr_number}"
        latest = pr_versions[-1]
        pr_reviews = reviews_by_pr.get((instance, repository, pr_number), [])
        lines = [
            f"Pull request {reference}: {latest['title']}",
            f"Source: code host ({instance})",
            f"Opened: {iso(latest['created_at'])} by {latest['author_name'] or latest['author_source_id'] or '(unknown)'}, "
            f"final state: {latest['state']}",
            "",
        ]
        for version in pr_versions:
            lines.append(f"[{reference} v{version['version_number']}] {iso(version['version_at'])}, state: {version['state']}, "
                         f"head commit {version['head_commit']}")
            lines.append(f"Title: {version['title']}")
            if version["description"]:
                lines.append(f"Description: {version['description']}")
            changes = as_list(version["code_changes"])
            if changes:
                lines.append("Code changes:")
                for change in changes:
                    lines.append(f"- {change.get('file_path', '(no path)')} ({change.get('change_type') or 'changed'})")
                    if change.get("before_summary"):
                        lines.append(f"  Before: {change['before_summary']}")
                    if change.get("after_summary"):
                        lines.append(f"  After: {change['after_summary']}")
                    if change.get("diff"):
                        lines.append("  Diff:")
                        lines += [f"    {line}" for line in str(change["diff"]).replace("\\n", "\n").splitlines()]
            lines.append("")
        if pr_reviews:
            lines.append("Reviews:")
            for review in pr_reviews:
                details = [review["entry_type"], f"on version {review['pr_version_number']}"]
                if review["file_path"]:
                    location = review["file_path"]
                    if review["line_number"]:
                        location += f" line {review['line_number']} ({review['diff_side']})"
                    details.append(location)
                if review["reply_to_source_id"]:
                    details.append(f"reply to {review['reply_to_source_id']}")
                lines.append(f"[{review['source_id']}] {iso(review['created_at'])} "
                             f"{review['author_name'] or review['author_source_id'] or '(unknown)'}, {', '.join(details)}:")
                lines += [review["body"] or "(no text)", ""]
        documents.append(document(
            f"pr:{instance}:{reference}", "pull_requests", f"{reference}: {latest['title']}", lines,
            [reference] + [f"{reference} v{v['version_number']}" for v in pr_versions] + [r["source_id"] for r in pr_reviews],
            [latest["created_at"]] + [v["version_at"] for v in pr_versions] + [r["created_at"] for r in pr_reviews],
        ))
    return documents


def build_documents(rows: dict[str, list[dict]]) -> list[dict]:
    return (
        mail_documents(rows["mail"])
        + slack_documents(rows["slack"])
        + teams_documents(rows["meetings"], rows["segments"])
        + issue_documents(rows["issues"], rows["issue_versions"], rows["issue_comments"])
        + document_documents(rows["document_versions"])
        + pull_request_documents(rows["pr_versions"], rows["pr_reviews"])
    )


# ---------------------------------------------------------------------------
# Export and state.
# ---------------------------------------------------------------------------

class ExportRunningError(RuntimeError):
    pass


# One export at a time: a second request while one runs is refused instead of writing the same file at once.
_export_lock = threading.Lock()


def content_hash(content: bytes) -> str:
    """The input's fingerprint. A later index can record which input it was built from and tell when it is stale."""
    return hashlib.sha256(content).hexdigest()


def export_input(database_url: str, path: Path = INPUT_FILE) -> dict:
    """Writes the input file only when its content changed; the same data leaves the file and its time untouched."""
    if not _export_lock.acquire(blocking=False):
        raise ExportRunningError("An export is already running. Try again when it is done.")
    try:
        documents = build_documents(read_sources(database_url))
        content = "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in documents).encode("utf-8")
        changed = not path.exists() or content_hash(path.read_bytes()) != content_hash(content)
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".jsonl.tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        return {**input_state(path), "changed": changed}
    finally:
        _export_lock.release()


def input_state(path: Path = INPUT_FILE) -> dict:
    if not path.exists():
        return {"exported_at": None, "path": str(path.relative_to(PROJECT_ROOT)), "content_hash": None, "counts": {},
                "documents": []}

    content = path.read_bytes()
    documents = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    counts = {source: sum(1 for doc in documents if doc["source"] == source) for source in SOURCE_ORDER}
    return {
        "exported_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "path": str(path.relative_to(PROJECT_ROOT)),
        "content_hash": content_hash(content),
        "counts": counts,
        "total_characters": sum(len(doc["text"]) for doc in documents),
        "documents": [
            {**doc, "source_label": SOURCE_LABELS.get(doc["source"], doc["source"]), "characters": len(doc["text"])}
            for doc in documents
        ],
    }
