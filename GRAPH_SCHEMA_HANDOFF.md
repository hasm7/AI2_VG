# Graph Schema Handoff (Live Snapshot)

This file is a live introspection of the actual Neo4j database, not a description of intent. It exists so an LLM or a human can decide which indexes are worth creating and how traversal queries should be shaped, without re-deriving the schema from code.

For *why* the model looks like this — provenance from SQL, relationship semantics, identity resolution rules, re-runnability — read `docs/GRAPH_DATA_HANDOFF.md`. This file only answers *what is actually in the database right now*: labels, properties, data types, existing constraints/indexes, and relationship endpoint pairs.

Snapshot taken: 2026-09-20, database `neo4j` at `NEO4J_URI` from `.env`, via `scripts/inspect_neo4j.py` and a one-off read-only introspection query (`MATCH (n) RETURN labels(n), properties(n)` per label, `MATCH (a)-[r]->(b) RETURN ...` per relationship type). The dataset is small simulated test data (single-digit to low-double-digit node counts per label), so treat property *presence* as representative of the schema, not the counts as representative of production scale.

Type notation: types are inferred from live Python/Neo4j driver values (`string`, `integer`, `float`, `boolean`, `datetime`, `list<string>`, `map`). All timestamp-like properties (`*_at`) are stored as **ISO 8601 strings**, not Neo4j temporal types — they were written as `str(...)` during import. `list<empty>` means the property was an empty list on at least one sampled node (e.g. `Person.emails` for a name-only identity).

---

## 1. Node labels: properties and types

### `Person` (7 nodes)

| Property | Type | Notes |
| --- | --- | --- |
| `person_key` | string | Unique constraint key. `"email:..."`, `"source:..."`, or `"name:..."`. |
| `name` | string | Best display name. |
| `email` | string | Primary email, or absent if none. |
| `source_id` | string | Primary source ID, or absent if none. |
| `emails` | list\<string\> | May be empty list. |
| `source_ids` | list\<string\> | May be empty list. |
| `names` | list\<string\> | Always non-empty. |
| `identity_confidence` | string | `"strong"` or `"weak"`. |
| `identity_ambiguous` | boolean | |
| `actor_type` | string | `"person"` or `"mailbox"`. |

### `MailMessage` (4 nodes)

| Property | Type |
| --- | --- |
| `source_instance` | string |
| `message_id` | string |
| `name`, `display_name` | string (= `message_id`) |
| `sender_address` | string |
| `sender_name` | string |
| `recipients_raw` | string (JSON-encoded array) |
| `subject` | string |
| `body` | string |
| `sent_at` | string (ISO datetime) |
| `in_reply_to_id` | string (optional) |
| `source_url` | string |

### `SlackMessage` (12 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `workspace_id`, `channel_id`, `message_id` | string |
| `version_number` | integer |
| `channel_name` | string |
| `name`, `display_name` | string (= `message_id`) |
| `author_source_id`, `author_name`, `author_email` | string |
| `body` | string |
| `sent_at`, `version_at` | string (ISO datetime) |
| `thread_root_id` | string (optional) |
| `source_url` | string |

### `TeamsMeeting` (2 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `meeting_id` | string |
| `name`, `display_name` | string (= `meeting_id`) |
| `title` | string |
| `started_at` | string (ISO datetime) |
| `ended_at` | string (ISO datetime, optional) |
| `participants_raw` | string (JSON-encoded array) |
| `source_url` | string |

### `TeamsTranscriptSegment` (9 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `meeting_id`, `segment_id` | string |
| `name`, `display_name` | string (= `segment_id`) |
| `sequence_number` | integer |
| `speaker_source_id`, `speaker_name` | string |
| `start_offset_ms` | integer |
| `end_offset_ms` | integer (optional) |
| `body` | string |

### `Issue` (2 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `issue_id`, `issue_key` | string |
| `name`, `display_name` | string (= `issue_key`) |
| `issue_type`, `title`, `description`, `acceptance_criteria`, `status`, `priority` | string (from latest version) |
| `creator_name`, `creator_source_id` | string |
| `assignee_name`, `assignee_source_id` | string (from latest version) |
| `created_at`, `version_at` | string (ISO datetime) |
| `version_number` | integer |
| `version_count` | integer |
| `versions_raw` | string (JSON-encoded array, ascending) |
| `comment_count` | integer |
| `comments_raw` | string (JSON-encoded array) |
| `source_url` | string |

### `IssueVersion` (7 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `issue_id` | string |
| `version_number` | integer |
| `issue_type`, `title`, `description`, `acceptance_criteria`, `status`, `priority` | string |
| `assignee_source_id`, `assignee_name`, `changed_by_id`, `changed_by_name` | string |
| `version_at` | string (ISO datetime) |
| `source_url` | string |
| `name`, `display_name` | string (`"<issue_key> v<version_number>"`) |

### `IssueComment` (5 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `comment_id`, `issue_id` | string |
| `author_source_id`, `author_name` | string |
| `body` | string |
| `version_number` | integer |
| `version_count` | integer |
| `reply_to_comment_id` | string (optional) |
| `created_at`, `version_at` | string (ISO datetime) |
| `source_url` | string |
| `name`, `display_name` | string (= `comment_id`) |

### `Document` (2 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `document_id` | string |
| `name`, `display_name` | string (= `document_id`) |
| `document_type`, `title`, `body`, `content_format` | string (from latest version) |
| `author_name`, `author_source_id` | string |
| `created_at`, `version_at` | string (ISO datetime) |
| `version_number` | integer |
| `change_summary` | string |
| `versions_raw` | string (JSON-encoded array, earlier versions) |
| `source_url` | string |

### `DocumentVersion` (3 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `document_id` | string |
| `version_number` | integer |
| `document_type`, `title`, `body`, `content_format` | string |
| `author_source_id`, `author_name` | string |
| `created_at`, `version_at` | string (ISO datetime) |
| `change_summary` | string |
| `source_url` | string |
| `name`, `display_name` | string (`"<document_id> v<version_number>"`) |

### `PullRequest` (2 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `repository` | string |
| `pr_number` | integer |
| `name`, `display_name` | string (`"<repository>#<pr_number>"`) |
| `title`, `description`, `state` | string (from latest version) |
| `author_name`, `author_source_id` | string |
| `created_at`, `version_at` | string (ISO datetime) |
| `version_number` | integer |
| `base_commit`, `head_commit` | string |
| `code_changes_raw` | string (JSON-encoded array) |
| `reviews_raw` | string (JSON-encoded array) |
| `source_url` | string |

### `PullRequestReview` (6 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `repository` | string |
| `pr_number`, `pr_version_number`, `version_count`, `version_number` | integer |
| `source_id`, `entry_type` | string |
| `reviewed_commit`, `author_source_id`, `author_name`, `body` | string |
| `reply_to_source_id`, `review_group_id`, `file_path`, `diff_side` | string (optional) |
| `line_number` | integer (optional) |
| `created_at`, `version_at` | string (ISO datetime) |
| `source_url` | string |
| `name`, `display_name` | string (`"<repository>#<pr_number> <source_id>"`) |

### `CodeChange` (7 nodes)

| Property | Type |
| --- | --- |
| `source_instance`, `repository` | string |
| `pr_number`, `version_number` | integer |
| `file_path`, `change_type`, `before_summary`, `after_summary`, `diff` | string |
| `name`, `display_name` | string (`"<repository>#<pr_number> <file_path>"`) |

### `Topic` (1 node) — derived, Task 06

| Property | Type |
| --- | --- |
| `slug`, `name`, `topic_type`, `summary`, `display_name` | string |
| `derived` | boolean (`true`) |
| `generated_by`, `model` | string |
| `generated_at` | string (ISO datetime) |

### `Event` (10 nodes) — derived, Task 06

| Property | Type |
| --- | --- |
| `topic_slug`, `slug`, `name`, `event_type`, `summary`, `display_name` | string |
| `occurred_at` | string (ISO datetime) |
| `derived` | boolean (`true`) |
| `generated_by`, `model` | string |
| `generated_at` | string (ISO datetime) |

### `PipelineState` (1 node) — bookkeeping singleton, excluded from graph API

| Property | Type |
| --- | --- |
| `id` | string (`"singleton"`) |
| `last_import_at`, `last_extraction_at`, `last_layer_build_at` | string (ISO datetime) |

---

## 2. Constraints and indexes that already exist

All of these are `RANGE` node-property uniqueness constraints (each constraint auto-creates a backing index). Verified live via `SHOW CONSTRAINTS` / `SHOW INDEXES`.

| Label | Unique key (constraint = index) |
| --- | --- |
| `Person` | `person_key` |
| `MailMessage` | `(source_instance, message_id)` |
| `SlackMessage` | `(source_instance, workspace_id, channel_id, message_id, version_number)` |
| `TeamsMeeting` | `(source_instance, meeting_id)` |
| `TeamsTranscriptSegment` | `(source_instance, meeting_id, segment_id)` |
| `Issue` | `(source_instance, issue_id)` |
| `IssueVersion` | `(source_instance, issue_id, version_number)` |
| `IssueComment` | `(source_instance, comment_id)` |
| `Document` | `(source_instance, document_id)` |
| `DocumentVersion` | `(source_instance, document_id, version_number)` |
| `PullRequest` | `(source_instance, repository, pr_number)` |
| `PullRequestReview` | `(source_instance, repository, pr_number, source_id)` |
| `CodeChange` | `(source_instance, repository, pr_number, version_number, file_path)` |
| `Topic` | `slug` |
| `Event` | `(topic_slug, slug)` |

Plus two default token lookup indexes (`index_1b9dcc97` on relationship types, `index_460996c0` on node labels) that Neo4j creates automatically — not application-specific.

**No fulltext index, no vector index, and no index on any property outside the primary key tuples above exists yet.** In particular, there is currently no index on `Issue.issue_key`, `PullRequest.(repository, pr_number)` as a lookup outside the exact constraint tuple, `*.body`/`*.title` text fields, or any `*_at` timestamp property — all traversal and filtering on those currently relies on constraint lookups (exact key match only) or unindexed scans.

---

## 3. Relationship types: endpoints and properties

`from -> to` shows every observed label pair for that relationship type (a type can have more than one valid pair, e.g. `DERIVED_FROM` and `MENTIONS_*` fan out to many target labels).

| Relationship | from → to | Properties |
| --- | --- | --- |
| `SENT_MAIL` | `Person → MailMessage` | — |
| `MAIL_RECIPIENT` | `MailMessage → Person` | `recipient_type` (string) |
| `SENT_SLACK_MESSAGE` | `Person → SlackMessage` | — |
| `SLACK_THREAD_REPLY_TO` | `SlackMessage → SlackMessage` | — |
| `PARTICIPATED_IN_MEETING` | `Person → TeamsMeeting` | — |
| `HAS_TEAMS_TRANSCRIPT_SEGMENT` | `TeamsMeeting → TeamsTranscriptSegment` | — |
| `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` | `Person → TeamsTranscriptSegment` | — |
| `CREATED_ISSUE` | `Person → Issue` | — |
| `OWNS_ISSUE` | `Person → Issue` | — |
| `COMMENTED_ON_ISSUE` | `Person → Issue` | — |
| `HAS_ISSUE_VERSION` | `Issue → IssueVersion` | — |
| `NEXT_ISSUE_VERSION` | `IssueVersion → IssueVersion` | — |
| `CHANGED_ISSUE_VERSION` | `Person → IssueVersion` | — |
| `HAS_ISSUE_COMMENT` | `Issue → IssueComment` | — |
| `WROTE_ISSUE_COMMENT` | `Person → IssueComment` | — |
| `REPLY_TO_ISSUE_COMMENT` | `IssueComment → IssueComment` | — |
| `AUTHORED_DOCUMENT` | `Person → Document` | — |
| `HAS_DOCUMENT_VERSION` | `Document → DocumentVersion` | — |
| `NEXT_DOCUMENT_VERSION` | `DocumentVersion → DocumentVersion` | — |
| `AUTHORED_DOCUMENT_VERSION` | `Person → DocumentVersion` | — |
| `AUTHORED_PR` | `Person → PullRequest` | — |
| `REVIEWED_PR` | `Person → PullRequest` | — |
| `HAS_PR_REVIEW` | `PullRequest → PullRequestReview` | — |
| `WROTE_PR_REVIEW` | `Person → PullRequestReview` | — |
| `REPLY_TO_PR_REVIEW` | `PullRequestReview → PullRequestReview` | — |
| `HAS_CODE_CHANGE` | `PullRequest → CodeChange` | — |
| `MENTIONS_ISSUE` | `{Document, DocumentVersion, Issue, IssueVersion, MailMessage, PullRequest, SlackMessage, TeamsTranscriptSegment} → Issue` | `derived` (bool), `extracted_by`, `matched_text`, `source_property` (string), `extracted_at` (datetime string) |
| `MENTIONS_PULL_REQUEST` | `{Document, DocumentVersion, Issue, IssueComment, IssueVersion, PullRequest, PullRequestReview, SlackMessage, TeamsTranscriptSegment} → PullRequest` | same shape as `MENTIONS_ISSUE` |
| `MENTIONS_DOCUMENT` | `{Document, DocumentVersion, IssueComment, IssueVersion, PullRequest, PullRequestReview, SlackMessage, TeamsTranscriptSegment} → Document` | same shape as `MENTIONS_ISSUE` |
| `ABOUT_TOPIC` | `Issue → Topic` | `derived`, `generated_by`, `generated_at` |
| `DERIVED_FROM` | `Topic → {CodeChange, Document, DocumentVersion, Issue, IssueComment, IssueVersion, MailMessage, PullRequest, PullRequestReview, SlackMessage, TeamsMeeting, TeamsTranscriptSegment}` | `derived`, `generated_by`, `generated_at` |
| `EVENT_OF_TOPIC` | `Event → Topic` | `derived`, `generated_by`, `generated_at` |
| `EVIDENCED_BY` | `Event → {CodeChange, DocumentVersion, Issue, IssueComment, IssueVersion, PullRequest, PullRequestReview, SlackMessage, TeamsTranscriptSegment}` | `derived`, `generated_by`, `generated_at` |
| `CAUSED` | `Event → Event` | `derived`, `evidence` (list\<string\>), `explanation` (string), `generated_by`, `generated_at` |
| `ACTED_IN_EVENT` | `Person → Event` | `derived`, `generated_by`, `generated_at` |

Every relationship listed with `derived: true` in its properties was written by an extraction pass (`reference-extraction-v1` or `topic-event-extraction-v1`), never by an import — see `docs/GRAPH_DATA_HANDOFF.md` for re-runnability semantics before reasoning about how stable these edges are.

---

## 4. What this snapshot does not tell you

- **No fulltext or vector index exists** on any `body`/`title`/`description` property. If retrieval needs semantic or keyword search over content, that is greenfield, not something to tune.
- **Counts above are test-fixture scale** (2–12 nodes per label). Selectivity, cardinality, and which indexes pay for themselves cannot be judged from this data volume — judge from the *shape* of the model (star pattern around `Issue`/`PullRequest`/`Document`, hub-heavy `Person`, fan-out `Topic`/`Event`) instead of from row counts.
- This snapshot does not include property existence constraints, only uniqueness constraints — none of the "Required" columns documented per SQL table in `docs/SQL_DATA_HANDOFF.md` are enforced as `IS NOT NULL` in Neo4j.
- Regenerate this file by rerunning the introspection (`scripts/inspect_neo4j.py` for constraints/indexes/counts; a per-label `MATCH (n:Label) RETURN properties(n)` scan for the property/type tables above) whenever the import code in `viewer/app.py`, `backend/reference_extraction.py`, or `backend/topic_event_extraction.py` changes what it writes.
