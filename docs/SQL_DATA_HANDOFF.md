# SQL Data Handoff Specification

This document describes the PostgreSQL source-preserving layer as it exists in the local database and in `scripts/setup_postgres_schema.py`.

PostgreSQL is the source of truth. Neo4j is derived from it and can be rebuilt. The SQL layer deliberately keeps the original source-system boundaries instead of flattening everything into a generic event table.

## Current Data Snapshot

Read-only inventory from the local PostgreSQL database:

| Table | Rows | Logical source |
| --- | ---: | --- |
| `mail_messages` | 4 | Mail |
| `slack_messages` | 12 | Slack / project chat |
| `teams_meetings` | 2 | Teams meetings |
| `teams_transcript_segments` | 9 | Teams transcripts |
| `issues` | 2 | Issues / tickets |
| `issue_versions` | 7 | Issue lifecycle history |
| `issue_comments` | 5 | Issue comments |
| `document_versions` | 3 | Requirements and technical documentation |
| `pr_versions` | 4 | Pull requests |
| `pr_reviews` | 6 | PR reviews and code comments |

The data tells one connected product-engineering story around administrator session lifetime:

- `AUTH-17`: administrator sessions expire too early during long tasks; created on `2026-02-26`; 5 issue versions; final status `done`.
- `AUTH-19`: mobile administrators still expire while active; created on `2026-03-16`; 2 issue versions; current status `in progress`.
- `REQ-AUTH-SESSION`: requirement document with 2 versions, rewritten after the March 3 refinement call.
- `doc-002`: technical design for the session refresh path.
- `backend-api#42`: PR for `AUTH-17`, 3 versions, merged.
- `backend-api#47`: PR for `AUTH-19`, 1 version, open.

## Source Groups

There are six logical data sources and ten SQL tables:

| Logical source | SQL tables | Purpose |
| --- | --- | --- |
| Mail | `mail_messages` | Email messages, recipients, and reply-chain IDs. |
| Slack / project chat | `slack_messages` | Channel messages, thread replies, and edited message versions. |
| Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` | Meeting metadata and ordered transcript segments. |
| Issues / tickets | `issues`, `issue_versions`, `issue_comments` | Stable issue identity, lifecycle state versions, and comment versions. |
| Requirements and technical documentation | `document_versions` | Versioned requirement/design/runbook/decision documents. |
| Pull requests, reviews, and code changes | `pr_versions`, `pr_reviews` | PR state versions, review decisions, line comments, and changed-code JSON. |

## SQL to Graph Summary

The graph import in `viewer/app.py` maps source tables into Neo4j as follows:

| SQL table or field | Neo4j output |
| --- | --- |
| `mail_messages` | `MailMessage` nodes; `SENT_MAIL`; `MAIL_RECIPIENT`. |
| `slack_messages` | One `SlackMessage` node per version; `SENT_SLACK_MESSAGE`; `SLACK_THREAD_REPLY_TO`. |
| `teams_meetings` | `TeamsMeeting` nodes; `PARTICIPATED_IN_MEETING`. |
| `teams_transcript_segments` | `TeamsTranscriptSegment` nodes; `HAS_TEAMS_TRANSCRIPT_SEGMENT`; `SPOKE_TEAMS_TRANSCRIPT_SEGMENT`. |
| `issues` | Parent `Issue` nodes; creator fields feed `CREATED_ISSUE`. |
| `issue_versions` | Parent `Issue` latest-state properties; `IssueVersion` nodes; `HAS_ISSUE_VERSION`; `NEXT_ISSUE_VERSION`; `CHANGED_ISSUE_VERSION`; assignee feeds `OWNS_ISSUE`. |
| `issue_comments` | `IssueComment` nodes from latest comment row; `HAS_ISSUE_COMMENT`; `WROTE_ISSUE_COMMENT`; `REPLY_TO_ISSUE_COMMENT`; authors feed `COMMENTED_ON_ISSUE`. |
| `document_versions` | Parent `Document` latest-state node; all `DocumentVersion` nodes; `AUTHORED_DOCUMENT`; `HAS_DOCUMENT_VERSION`; `NEXT_DOCUMENT_VERSION`; `AUTHORED_DOCUMENT_VERSION`. |
| `pr_versions` | Parent `PullRequest` latest-state node; `AUTHORED_PR`; `CodeChange` nodes from each `code_changes` array entry; `HAS_CODE_CHANGE`. |
| `pr_reviews` | `PullRequestReview` nodes from latest review rows; `REVIEWED_PR`; `HAS_PR_REVIEW`; `WROTE_PR_REVIEW`; `REPLY_TO_PR_REVIEW`. |
| Person-bearing fields across all tables | Shared `Person` nodes resolved by `viewer/person_identity.py`. |

Do not add SQL tables only to make graph traversal easier. SQL tables should preserve source records. Graph-only retrieval units, derived references, embeddings, topics, and events belong in Neo4j/backend layers.

## General Rules

- Use stable source IDs from the simulated source system.
- Use `source_instance` to identify the simulated source system instance, such as `gmail-main`, `slack-main`, `teams-main`, `jira-main`, `docs-main`, or `github-main`.
- Use timezone-aware timestamps for every `TIMESTAMPTZ`.
- Preserve history with multiple rows using the same stable object ID and increasing `version_number`.
- JSONB array fields must contain JSON arrays, not stringified JSON.
- Cross-source links are preserved through explicit text references such as `AUTH-17`, `backend-api#42`, and `REQ-AUTH-SESSION`; those references are extracted later into Neo4j.

## Table Details

### `mail_messages`

One row is one email message.

Primary key: `(source_instance, message_id)`.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | yes | Source system instance. |
| `message_id` | `TEXT` | yes | Stable email ID. |
| `sender_address` | `TEXT` | yes | Sender email. |
| `sender_name` | `TEXT` | no | Sender display name. |
| `recipients` | `JSONB` | yes | Array of recipient objects. |
| `subject` | `TEXT` | no | Email subject. |
| `body` | `TEXT` | yes | Email body. |
| `sent_at` | `TIMESTAMPTZ` | yes | Send time. |
| `in_reply_to_id` | `TEXT` | no | Parent email `message_id`; indexed but not a foreign key. |
| `source_url` | `TEXT` | no | Link back to source. |

Checks and indexes:

- `CHECK (jsonb_typeof(recipients) = 'array')`
- `idx_mail_reply` on `(source_instance, in_reply_to_id)`

Current rows:

| ID | Sender | Subject | Time | Reply to |
| --- | --- | --- | --- | --- |
| `mail-001` | Martin Ek | Administrators logged out during audit exports | `2026-02-24 09:12+01` | |
| `mail-002` | Anna | Fwd: Administrators logged out during audit exports | `2026-02-24 11:40+01` | `mail-001` |
| `mail-003` | Anna Berg | Re: Administrators logged out during audit exports | `2026-03-13 08:30+01` | `mail-001` |
| `mail-004` | Martin Ek | Re: Administrators logged out during audit exports | `2026-03-16 14:05+01` | `mail-003` |

### `slack_messages`

One row is one version of one Slack message. Edited messages keep previous versions.

Primary key: `(source_instance, workspace_id, channel_id, message_id, version_number)`.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | yes | Source system instance. |
| `message_id` | `TEXT` | yes | Stable message ID. |
| `version_number` | `INTEGER` | yes | Starts at 1; `> 0`. |
| `workspace_id` | `TEXT` | yes | Workspace ID. |
| `channel_id` | `TEXT` | yes | Channel ID. |
| `channel_name` | `TEXT` | no | Human-readable channel name. |
| `author_source_id` | `TEXT` | no | Source ID for author. |
| `author_name` | `TEXT` | no | Display name. |
| `author_email` | `TEXT` | no | Email when known. |
| `body` | `TEXT` | yes | Message body. |
| `sent_at` | `TIMESTAMPTZ` | yes | Original send time. |
| `version_at` | `TIMESTAMPTZ` | yes | Version timestamp; must be `>= sent_at`. |
| `thread_root_id` | `TEXT` | no | Root message ID for thread replies. |
| `source_url` | `TEXT` | no | Link back to source. |

Checks and indexes:

- `CHECK (version_number > 0)`
- `CHECK (version_at >= sent_at)`
- `idx_slack_thread` on `(source_instance, workspace_id, channel_id, thread_root_id, sent_at)`

Current data:

- All rows are in `slack-main`, workspace `w-example`, channel `auth-platform`.
- Messages `slack-001` through `slack-011` exist.
- `slack-006` has two versions.
- `slack-008` replies to `slack-007`.
- `slack-011` replies to `slack-010`.
- Authors represented: Erik Nilsson, Anna, Anna Berg, Anna Lindqvist, Priya Raman.

### `teams_meetings`

One row is one Teams meeting.

Primary key: `(source_instance, meeting_id)`.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | yes | Source system instance. |
| `meeting_id` | `TEXT` | yes | Stable meeting ID. |
| `title` | `TEXT` | yes | Meeting title. |
| `started_at` | `TIMESTAMPTZ` | yes | Start time. |
| `ended_at` | `TIMESTAMPTZ` | no | Must be `>= started_at` when present. |
| `participants` | `JSONB` | yes | Array, defaults to `[]`. |
| `source_url` | `TEXT` | no | Link back to source. |

Checks:

- `CHECK (ended_at IS NULL OR ended_at >= started_at)`
- `CHECK (jsonb_typeof(participants) = 'array')`

Current rows:

| ID | Title | Time | Participant count |
| --- | --- | --- | ---: |
| `meet-001` | Auth refinement: administrator session lifetime | `2026-03-03 13:00-13:45+01` | 4 |
| `meet-002` | Sprint review | `2026-03-12 10:00-10:30+01` | 3 |

### `teams_transcript_segments`

One row is one ordered transcript segment inside a meeting.

Primary key: `(source_instance, meeting_id, segment_id)`.

Unique key: `(source_instance, meeting_id, sequence_number)`.

Foreign key: `(source_instance, meeting_id)` references `teams_meetings`.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | yes | Must match parent meeting. |
| `meeting_id` | `TEXT` | yes | Parent meeting. |
| `segment_id` | `TEXT` | yes | Stable segment ID. |
| `sequence_number` | `INTEGER` | yes | Ordered position; `> 0`. |
| `speaker_source_id` | `TEXT` | no | Speaker source ID. |
| `speaker_name` | `TEXT` | no | Speaker display name. |
| `start_offset_ms` | `BIGINT` | yes | Offset from meeting start; `>= 0`. |
| `end_offset_ms` | `BIGINT` | no | Must be `>= start_offset_ms` when present. |
| `body` | `TEXT` | yes | Transcript text. |

Current rows:

- `meet-001`: `seg-001` to `seg-006`, speakers Anna Berg, Anna, Priya Raman, Erik Nilsson.
- `meet-002`: `seg-007` to `seg-009`, speakers Anna Berg, Anna Lindqvist, Erik Nilsson.

### `issues`

One row is one stable issue identity. Mutable state is in `issue_versions`.

Primary key: `(source_instance, issue_id)`.

Unique key: `(source_instance, issue_key)`.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | yes | Source system instance. |
| `issue_id` | `TEXT` | yes | Stable source ID. |
| `issue_key` | `TEXT` | yes | Human-readable key, e.g. `AUTH-17`. |
| `created_at` | `TIMESTAMPTZ` | yes | Issue creation time. |
| `creator_source_id` | `TEXT` | no | Creator source ID. |
| `creator_name` | `TEXT` | no | Creator display name. |
| `source_url` | `TEXT` | no | Link back to source. |

Current rows:

| Issue ID | Key | Created | Creator | Versions |
| --- | --- | --- | --- | ---: |
| `issue-001` | `AUTH-17` | `2026-02-26 10:15+01` | Anna Berg | 5 |
| `issue-002` | `AUTH-19` | `2026-03-16 15:20+01` | Erik Nilsson | 2 |

### `issue_versions`

One row is one version of an issue's state.

Primary key: `(source_instance, issue_id, version_number)`.

Unique key: `(source_instance, issue_id, version_at)`.

Foreign key: `(source_instance, issue_id)` references `issues`.

Index: `idx_issue_versions_history` on `(source_instance, issue_id, version_number)`.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | yes | Source system instance. |
| `issue_id` | `TEXT` | yes | Parent issue. |
| `version_number` | `INTEGER` | yes | Starts at 1; `> 0`. |
| `issue_type` | `TEXT` | yes | Story, bug, task, epic, etc. |
| `title` | `TEXT` | yes | Versioned title. |
| `description` | `TEXT` | no | Versioned description. |
| `acceptance_criteria` | `TEXT` | no | Versioned acceptance criteria. |
| `status` | `TEXT` | yes | Versioned status. |
| `priority` | `TEXT` | no | Versioned priority. |
| `assignee_source_id` | `TEXT` | no | Assignee source ID. |
| `assignee_name` | `TEXT` | no | Assignee name. |
| `changed_by_id` | `TEXT` | no | Person who made the version change. |
| `changed_by_name` | `TEXT` | no | Display name of changer. |
| `version_at` | `TIMESTAMPTZ` | yes | Version timestamp. |
| `source_url` | `TEXT` | no | Link back to source. |

Current lifecycle:

| Issue | Version | Status | Priority | Assignee | Changed by | Time |
| --- | ---: | --- | --- | --- | --- | --- |
| `AUTH-17` | 1 | open | medium | | Anna Berg | `2026-02-26 10:15+01` |
| `AUTH-17` | 2 | in progress | medium | Anna Lindqvist | Anna Lindqvist | `2026-02-27 09:40+01` |
| `AUTH-17` | 3 | blocked | high | Anna Lindqvist | Anna Berg | `2026-03-03 14:25+01` |
| `AUTH-17` | 4 | in progress | high | Anna Lindqvist | Anna Lindqvist | `2026-03-10 09:35+01` |
| `AUTH-17` | 5 | done | high | Anna Lindqvist | Anna Lindqvist | `2026-03-11 15:55+01` |
| `AUTH-19` | 1 | open | high | | Erik Nilsson | `2026-03-16 15:20+01` |
| `AUTH-19` | 2 | in progress | high | Erik Nilsson | Erik Nilsson | `2026-03-16 16:05+01` |

### `issue_comments`

One row is one version of one issue comment.

Primary key: `(source_instance, comment_id, version_number)`.

Foreign key: `(source_instance, issue_id)` references `issues`.

Index: `idx_issue_comments_issue` on `(source_instance, issue_id, created_at)`.

Current rows:

| Comment | Issue | Author | Created | Reply to |
| --- | --- | --- | --- | --- |
| `comment-001` | `AUTH-17` | Anna Lindqvist | `2026-02-26 11:02+01` | |
| `comment-002` | `AUTH-17` | Priya Raman | `2026-03-03 14:30+01` | |
| `comment-003` | `AUTH-17` | Anna Berg | `2026-03-04 09:15+01` | `comment-002` |
| `comment-004` | `AUTH-17` | Anna Lindqvist | `2026-03-11 15:58+01` | |
| `comment-005` | `AUTH-19` | Anna Lindqvist | `2026-03-16 16:00+01` | |

### `document_versions`

One row is one document version.

Primary key: `(source_instance, document_id, version_number)`.

Checks:

- `CHECK (version_number > 0)`
- `CHECK (version_at >= created_at)`

Current rows:

| Document | Version | Type | Title | Author | Version time |
| --- | ---: | --- | --- | --- | --- |
| `doc-001` | 1 | requirement | REQ-AUTH-SESSION: Administrator session lifetime | Anna Berg | `2026-02-27 09:00+01` |
| `doc-001` | 2 | requirement | REQ-AUTH-SESSION: Administrator session lifetime | Anna Berg | `2026-03-03 16:10+01` |
| `doc-002` | 1 | technical-design | Session refresh path | Anna Lindqvist | `2026-03-10 10:00+01` |

### `pr_versions`

One row is one version of one pull request.

Primary key: `(source_instance, repository, pr_number, version_number)`.

Checks:

- `CHECK (pr_number > 0)`
- `CHECK (version_number > 0)`
- `CHECK (state IN ('open', 'closed', 'merged'))`
- `CHECK (version_at >= created_at)`
- `CHECK (jsonb_typeof(code_changes) = 'array')`

Current rows:

| PR | Version | State | Title | Author | Code changes | Version time |
| --- | ---: | --- | --- | --- | ---: | --- |
| `backend-api#42` | 1 | open | AUTH-17: raise administrator session expiry to sixty minutes | Anna Lindqvist | 2 | `2026-03-02 16:30+01` |
| `backend-api#42` | 2 | open | AUTH-17: inactivity based administrator session expiry | Anna Lindqvist | 1 | `2026-03-10 14:20+01` |
| `backend-api#42` | 3 | merged | AUTH-17: inactivity based administrator session expiry | Anna Lindqvist | 3 | `2026-03-11 15:45+01` |
| `backend-api#47` | 1 | open | AUTH-19: apply inactivity rules to the mobile refresh endpoint | Erik Nilsson | 1 | `2026-03-16 16:30+01` |

### `pr_reviews`

One row is one version of one PR review entry. It can be a whole-PR review decision or a line-specific code comment.

Primary key: `(source_instance, repository, pr_number, source_id, version_number)`.

Foreign key: `(source_instance, repository, pr_number, pr_version_number)` references `pr_versions`.

Index: `idx_pr_reviews_pr_version` on `(source_instance, repository, pr_number, pr_version_number)`.

Line-specific rule:

- If `line_number` is present, then `diff_side`, `file_path`, and `reviewed_commit` must also be present.
- If the review is not line-specific, then `line_number` and `diff_side` must both be `NULL`.

Current rows:

| Review | PR | Type | PR version | Author | File/line | Reply to |
| --- | --- | --- | ---: | --- | --- | --- |
| `review-001` | `backend-api#42` | changes_requested | 1 | Priya Raman | | |
| `review-002` | `backend-api#42` | comment | 1 | Priya Raman | `backend/auth/session.py:12 after` | |
| `review-003` | `backend-api#42` | comment | 1 | Anna Lindqvist | `backend/auth/session.py:12 after` | `review-002` |
| `review-004` | `backend-api#42` | approved | 3 | Priya Raman | | |
| `review-005` | `backend-api#42` | approved | 3 | Erik Nilsson | | |
| `review-006` | `backend-api#47` | comment | 1 | Anna Lindqvist | `backend/auth/mobile_refresh.py:18 after` | |

## Person Identity Fields

The graph importer resolves all person-bearing fields together before writing relationships:

| SQL location | Person fields | Email present |
| --- | --- | --- |
| `mail_messages` | `sender_address`, `sender_name` | yes |
| `mail_messages.recipients` JSONB | `address`, `name` | yes |
| `slack_messages` | `author_source_id`, `author_name`, `author_email` | yes |
| `teams_meetings.participants` JSONB | `source_id` or `id`, `name`, `email` | yes |
| `teams_transcript_segments` | `speaker_source_id`, `speaker_name` | no |
| `issues` | `creator_source_id`, `creator_name` | no |
| `issue_versions` | `assignee_source_id`, `assignee_name`, `changed_by_id`, `changed_by_name` | no |
| `issue_comments` | `author_source_id`, `author_name` | no |
| `document_versions` | `author_source_id`, `author_name` | no |
| `pr_versions` | `author_source_id`, `author_name` | no |
| `pr_reviews` | `author_source_id`, `author_name` | no |

The current graph resolves seven `Person` nodes: Anna Berg, Anna Lindqvist, Erik Nilsson, Martin Ek, Priya Raman, Support mailbox, and one ambiguous weak `Anna` name-only identity.

## Authoritative DDL

The authoritative DDL is the `SCHEMA_SQL` string in `scripts/setup_postgres_schema.py`. If this document and that file disagree, update this document from the script and then verify against the live database.
