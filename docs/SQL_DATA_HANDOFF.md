# SQL Data Handoff Specification

This document describes the PostgreSQL source schema for generating or loading simulated software engineering team data.

The most important rule is that the data must stay separated into six logical data sources. The database has nine tables because some sources need multiple tables, but the data should not be flattened into one generic event table.

## Source Groups

| Logical source | Tables | Purpose |
| --- | --- | --- |
| Mail | `mail_messages` | Email messages and reply chains. |
| Slack / project chat | `slack_messages` | Channel messages, threads, and edited message versions. |
| Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` | Meeting metadata and ordered transcript segments. |
| Issues / tickets | `issue_versions`, `issue_comments` | Ticket history, status, ownership, acceptance criteria, and comments. |
| Requirements and technical documentation | `document_versions` | Versioned requirements and technical documents. |
| Pull requests, code reviews, and code changes | `pr_versions`, `pr_reviews` | PR history, changed code metadata, review decisions, and code comments. |

## General Data Rules

- Use stable source IDs from the simulated source system. Do not invent new IDs for the same real-world object across versions.
- Use `source_instance` to identify the simulated source system instance, for example `gmail-main`, `slack-main`, `jira-main`, `docs-main`, or `github-main`.
- Use ISO-like timestamp values with timezone for all `TIMESTAMPTZ` fields, for example `2026-02-14T10:30:00+01:00`.
- Preserve versions by inserting multiple rows with the same object ID and increasing `version_number` where the current database constraints allow it.
- Current database note: `issue_versions` contains `version_number`, but the live schema also has `UNIQUE (source_instance, issue_id)`. That means the current database only allows one row per issue ID unless the schema is changed.
- Keep cross-source references in text and IDs when useful, but do not merge the six source groups into one table.
- JSONB array fields must contain JSON arrays, not strings containing JSON.

## Table Details

### 1. Mail: `mail_messages`

One row is one email message.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `message_id` | `TEXT` | Yes | Stable email ID. Part of primary key. |
| `sender_address` | `TEXT` | Yes | Email address of sender. |
| `sender_name` | `TEXT` | No | Display name of sender. |
| `recipients` | `JSONB` | Yes | JSON array of recipients. |
| `subject` | `TEXT` | No | Email subject. |
| `body` | `TEXT` | Yes | Email body. |
| `sent_at` | `TIMESTAMPTZ` | Yes | When the email was sent. |
| `in_reply_to_id` | `TEXT` | No | Message ID of the email this replies to. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, message_id)`

Indexes:

- `idx_mail_reply` on `(source_instance, in_reply_to_id)`

Recipient JSON example:

```json
[
  {"type": "to", "address": "owner@example.com", "name": "Product Owner"},
  {"type": "cc", "address": "team@example.com", "name": "Team"}
]
```

### 2. Slack / Project Chat: `slack_messages`

One row is one version of one Slack message. Edited messages should keep previous versions.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `message_id` | `TEXT` | Yes | Stable message ID. Part of primary key. |
| `version_number` | `INTEGER` | Yes | Starts at `1`; must be greater than `0`. Part of primary key. |
| `workspace_id` | `TEXT` | Yes | Slack workspace ID. Part of primary key. |
| `channel_id` | `TEXT` | Yes | Slack channel ID. Part of primary key. |
| `channel_name` | `TEXT` | No | Human-readable channel name. |
| `author_source_id` | `TEXT` | No | Source ID for author. |
| `author_name` | `TEXT` | No | Display name for author. |
| `author_email` | `TEXT` | No | Author email. |
| `body` | `TEXT` | Yes | Message body. |
| `sent_at` | `TIMESTAMPTZ` | Yes | Original send time. |
| `version_at` | `TIMESTAMPTZ` | Yes | Time for this version; must be greater than or equal to `sent_at`. |
| `thread_root_id` | `TEXT` | No | Root message ID for thread replies. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, workspace_id, channel_id, message_id, version_number)`

Indexes:

- `idx_slack_thread` on `(source_instance, workspace_id, channel_id, thread_root_id, sent_at)`

### 3. Teams / Meeting Transcripts

Teams data is split into meeting metadata and transcript segments.

#### `teams_meetings`

One row is one meeting.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `meeting_id` | `TEXT` | Yes | Stable meeting ID. Part of primary key. |
| `title` | `TEXT` | Yes | Meeting title. |
| `started_at` | `TIMESTAMPTZ` | Yes | Meeting start time. |
| `ended_at` | `TIMESTAMPTZ` | No | Must be greater than or equal to `started_at` when present. |
| `participants` | `JSONB` | Yes | JSON array; defaults to `[]`. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, meeting_id)`

Participants JSON example:

```json
[
  {"source_id": "u-anna", "name": "Anna Svensson", "email": "anna@example.com"},
  {"source_id": "u-erik", "name": "Erik Nilsson", "email": "erik@example.com"}
]
```

#### `teams_transcript_segments`

One row is one ordered transcript segment in a meeting.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Must match parent meeting. Part of primary key. |
| `meeting_id` | `TEXT` | Yes | Must match parent meeting. Part of primary key. |
| `segment_id` | `TEXT` | Yes | Stable segment ID. Part of primary key. |
| `sequence_number` | `INTEGER` | Yes | Ordered position in transcript; must be greater than `0`. |
| `speaker_source_id` | `TEXT` | No | Source ID for speaker. |
| `speaker_name` | `TEXT` | No | Speaker display name. |
| `start_offset_ms` | `BIGINT` | Yes | Offset from meeting start; must be greater than or equal to `0`. |
| `end_offset_ms` | `BIGINT` | No | Must be greater than or equal to `start_offset_ms` when present. |
| `body` | `TEXT` | Yes | Transcript text. |

Primary key: `(source_instance, meeting_id, segment_id)`

Unique key: `(source_instance, meeting_id, sequence_number)`

Foreign key:

- `(source_instance, meeting_id)` references `teams_meetings(source_instance, meeting_id)`

### 4. Issues / Tickets

Issue data is split into versioned issue state and comments.

#### `issue_versions`

One row is the current stored issue row for one issue in the live database.

Current database note: the table has `version_number`, but it also has `UNIQUE (source_instance, issue_id)`. Because of that unique constraint, do not generate multiple rows for the same issue unless the schema is changed first.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `issue_id` | `TEXT` | Yes | Stable issue ID. Part of primary key. |
| `version_number` | `INTEGER` | Yes | Starts at `1`; must be greater than `0`. Part of primary key. |
| `issue_key` | `TEXT` | Yes | Human-readable key, for example `AUTH-17`. |
| `issue_type` | `TEXT` | Yes | For example `story`, `bug`, `task`, or `epic`. |
| `title` | `TEXT` | Yes | Issue title. |
| `description` | `TEXT` | No | Issue description. |
| `acceptance_criteria` | `TEXT` | No | Completion criteria. |
| `status` | `TEXT` | Yes | Current status for this version. |
| `priority` | `TEXT` | No | Priority label. |
| `creator_source_id` | `TEXT` | No | Source ID for creator. |
| `creator_name` | `TEXT` | No | Creator display name. |
| `assignee_source_id` | `TEXT` | No | Source ID for assignee. |
| `assignee_name` | `TEXT` | No | Assignee display name. |
| `changed_by_id` | `TEXT` | No | Source ID for person who made this version change. |
| `changed_by_name` | `TEXT` | No | Name of person who made this version change. |
| `created_at` | `TIMESTAMPTZ` | Yes | Issue creation time. |
| `version_at` | `TIMESTAMPTZ` | Yes | Version time; must be greater than or equal to `created_at`. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, issue_id, version_number)`

Unique key: `(source_instance, issue_id)`

Important: `issue_comments` references `(source_instance, issue_id)`, so each issue ID must be unique within a source instance in the current schema.

#### `issue_comments`

One row is one version of one issue comment.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `comment_id` | `TEXT` | Yes | Stable comment ID. Part of primary key. |
| `version_number` | `INTEGER` | Yes | Starts at `1`; must be greater than `0`. Part of primary key. |
| `issue_id` | `TEXT` | Yes | Parent issue ID. |
| `author_source_id` | `TEXT` | No | Source ID for author. |
| `author_name` | `TEXT` | No | Author display name. |
| `body` | `TEXT` | Yes | Comment body. |
| `created_at` | `TIMESTAMPTZ` | Yes | Comment creation time. |
| `version_at` | `TIMESTAMPTZ` | Yes | Version time; must be greater than or equal to `created_at`. |
| `reply_to_comment_id` | `TEXT` | No | Parent comment ID for threaded replies. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, comment_id, version_number)`

Foreign key:

- `(source_instance, issue_id)` references `issue_versions(source_instance, issue_id)`

Indexes:

- `idx_issue_comments_issue` on `(source_instance, issue_id, created_at)`

### 5. Requirements and Technical Documentation: `document_versions`

One row is one version of one document.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `document_id` | `TEXT` | Yes | Stable document ID. Part of primary key. |
| `version_number` | `INTEGER` | Yes | Starts at `1`; must be greater than `0`. Part of primary key. |
| `document_type` | `TEXT` | Yes | For example `requirement`, `technical-design`, `decision-record`, or `runbook`. |
| `title` | `TEXT` | Yes | Document title. |
| `body` | `TEXT` | Yes | Document content. |
| `content_format` | `TEXT` | Yes | Defaults to `markdown`. |
| `author_source_id` | `TEXT` | No | Source ID for author. |
| `author_name` | `TEXT` | No | Author display name. |
| `created_at` | `TIMESTAMPTZ` | Yes | Document creation time. |
| `version_at` | `TIMESTAMPTZ` | Yes | Version time; must be greater than or equal to `created_at`. |
| `change_summary` | `TEXT` | No | Summary of this version change. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, document_id, version_number)`

### 6. Pull Requests, Code Reviews, and Code Changes

Pull request data is split into versioned PR state and reviews/comments.

#### `pr_versions`

One row is one version of one pull request.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `repository` | `TEXT` | Yes | Repository name. Part of primary key. |
| `pr_number` | `INTEGER` | Yes | PR number; must be greater than `0`. Part of primary key. |
| `version_number` | `INTEGER` | Yes | Starts at `1`; must be greater than `0`. Part of primary key. |
| `title` | `TEXT` | Yes | PR title. |
| `description` | `TEXT` | No | PR description. |
| `author_source_id` | `TEXT` | No | Source ID for author. |
| `author_name` | `TEXT` | No | Author display name. |
| `state` | `TEXT` | Yes | Must be one of `open`, `closed`, `merged`. |
| `created_at` | `TIMESTAMPTZ` | Yes | PR creation time. |
| `version_at` | `TIMESTAMPTZ` | Yes | Version time; must be greater than or equal to `created_at`. |
| `base_commit` | `TEXT` | Yes | Base commit hash or stable simulated commit ID. |
| `head_commit` | `TEXT` | Yes | Head commit hash or stable simulated commit ID. |
| `code_changes` | `JSONB` | Yes | JSON array of changed files or hunks. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, repository, pr_number, version_number)`

Code changes JSON example:

```json
[
  {
    "file_path": "backend/auth/session.py",
    "change_type": "modified",
    "before_summary": "Session timeout was fixed at 30 minutes.",
    "after_summary": "Admin sessions now use a 60 minute inactivity timeout.",
    "diff": "@@ ... simulated unified diff ..."
  }
]
```

#### `pr_reviews`

One row is one version of one PR review entry. It can be a whole-PR review decision or a specific code comment.

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_instance` | `TEXT` | Yes | Source system instance. Part of primary key. |
| `repository` | `TEXT` | Yes | Parent repository. Part of primary key. |
| `pr_number` | `INTEGER` | Yes | Parent PR number. Part of primary key. |
| `entry_type` | `TEXT` | Yes | Must be one of `comment`, `approved`, `changes_requested`, `commented`. |
| `source_id` | `TEXT` | Yes | Stable review/comment ID. Part of primary key. |
| `version_number` | `INTEGER` | Yes | Starts at `1`; must be greater than `0`. Part of primary key. |
| `pr_version_number` | `INTEGER` | Yes | Parent PR version being reviewed. |
| `reviewed_commit` | `TEXT` | No | Required for line-specific comments. |
| `author_source_id` | `TEXT` | No | Source ID for author. |
| `author_name` | `TEXT` | No | Author display name. |
| `body` | `TEXT` | No | Review body or comment text. |
| `created_at` | `TIMESTAMPTZ` | Yes | Review entry creation time. |
| `version_at` | `TIMESTAMPTZ` | Yes | Version time; must be greater than or equal to `created_at`. |
| `reply_to_source_id` | `TEXT` | No | Parent review/comment ID for replies. |
| `review_group_id` | `TEXT` | No | Groups comments belonging to the same review submission. |
| `file_path` | `TEXT` | No | Required for line-specific comments. |
| `line_number` | `INTEGER` | No | Required for line-specific comments; must be greater than `0`. |
| `diff_side` | `TEXT` | No | Required for line-specific comments; must be `before` or `after`. |
| `source_url` | `TEXT` | No | Link back to the simulated source. |

Primary key: `(source_instance, repository, pr_number, source_id, version_number)`

Foreign key:

- `(source_instance, repository, pr_number, pr_version_number)` references `pr_versions(source_instance, repository, pr_number, version_number)`

Indexes:

- `idx_pr_reviews_pr_version` on `(source_instance, repository, pr_number, pr_version_number)`

Line-specific review rule:

- If `line_number` is present, then `diff_side`, `file_path`, and `reviewed_commit` must also be present.
- If the review is not line-specific, then `line_number` and `diff_side` must both be `NULL`.

## Source Relationship Guidance

The SQL schema preserves original source material. It does not enforce all cross-source relationships with foreign keys, because those links are extracted later into the graph database.

When generating data, create natural links across sources through shared people, IDs, titles, and text references. For example:

- A customer email describes a requirement.
- Slack messages discuss the same requirement or issue key.
- A Teams meeting transcript mentions the decision.
- An issue captures the work and acceptance criteria.
- A document records the agreed requirement or technical design.
- A PR implements the issue and includes review comments.

Keep those links explicit in the source content where realistic:

- Mention issue keys such as `AUTH-17`.
- Mention PR numbers such as `backend-api#42`.
- Reuse person names and source IDs consistently.
- Use realistic timestamps so the sequence of events can be reconstructed.
- Preserve uncertainty, changed decisions, and disagreement when appropriate.

## Full DDL

The authoritative schema is defined in `scripts/setup_postgres_schema.py`. The DDL is reproduced here for handoff convenience.

```sql
CREATE TABLE mail_messages (
    source_instance     TEXT NOT NULL,
    message_id          TEXT NOT NULL,
    sender_address      TEXT NOT NULL,
    sender_name         TEXT,
    recipients          JSONB NOT NULL,
    subject             TEXT,
    body                TEXT NOT NULL,
    sent_at             TIMESTAMPTZ NOT NULL,
    in_reply_to_id      TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, message_id),
    CHECK (jsonb_typeof(recipients) = 'array')
);

CREATE TABLE slack_messages (
    source_instance     TEXT NOT NULL,
    message_id          TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    workspace_id        TEXT NOT NULL,
    channel_id          TEXT NOT NULL,
    channel_name        TEXT,
    author_source_id    TEXT,
    author_name         TEXT,
    author_email        TEXT,
    body                TEXT NOT NULL,
    sent_at             TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    thread_root_id      TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, workspace_id, channel_id, message_id, version_number),
    CHECK (version_at >= sent_at)
);

CREATE TABLE teams_meetings (
    source_instance     TEXT NOT NULL,
    meeting_id          TEXT NOT NULL,
    title               TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    participants        JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_url          TEXT,
    PRIMARY KEY (source_instance, meeting_id),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (jsonb_typeof(participants) = 'array')
);

CREATE TABLE teams_transcript_segments (
    source_instance     TEXT NOT NULL,
    meeting_id          TEXT NOT NULL,
    segment_id          TEXT NOT NULL,
    sequence_number     INTEGER NOT NULL CHECK (sequence_number > 0),
    speaker_source_id   TEXT,
    speaker_name        TEXT,
    start_offset_ms     BIGINT NOT NULL CHECK (start_offset_ms >= 0),
    end_offset_ms       BIGINT,
    body                TEXT NOT NULL,
    PRIMARY KEY (source_instance, meeting_id, segment_id),
    UNIQUE (source_instance, meeting_id, sequence_number),
    FOREIGN KEY (source_instance, meeting_id)
        REFERENCES teams_meetings (source_instance, meeting_id),
    CHECK (end_offset_ms IS NULL OR end_offset_ms >= start_offset_ms)
);

CREATE TABLE issue_versions (
    source_instance     TEXT NOT NULL,
    issue_id            TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    issue_key           TEXT NOT NULL,
    issue_type          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT,
    acceptance_criteria TEXT,
    status              TEXT NOT NULL,
    priority            TEXT,
    creator_source_id   TEXT,
    creator_name        TEXT,
    assignee_source_id  TEXT,
    assignee_name       TEXT,
    changed_by_id       TEXT,
    changed_by_name     TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    source_url          TEXT,
    PRIMARY KEY (source_instance, issue_id, version_number),
    UNIQUE (source_instance, issue_id),
    CHECK (version_at >= created_at)
);

CREATE TABLE issue_comments (
    source_instance     TEXT NOT NULL,
    comment_id          TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    issue_id            TEXT NOT NULL,
    author_source_id    TEXT,
    author_name         TEXT,
    body                TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    reply_to_comment_id TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, comment_id, version_number),
    FOREIGN KEY (source_instance, issue_id)
        REFERENCES issue_versions (source_instance, issue_id),
    CHECK (version_at >= created_at)
);

CREATE TABLE document_versions (
    source_instance     TEXT NOT NULL,
    document_id         TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    document_type       TEXT NOT NULL,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    content_format      TEXT NOT NULL DEFAULT 'markdown',
    author_source_id    TEXT,
    author_name         TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    change_summary      TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, document_id, version_number),
    CHECK (version_at >= created_at)
);

CREATE TABLE pr_versions (
    source_instance     TEXT NOT NULL,
    repository          TEXT NOT NULL,
    pr_number           INTEGER NOT NULL CHECK (pr_number > 0),
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    title               TEXT NOT NULL,
    description         TEXT,
    author_source_id    TEXT,
    author_name         TEXT,
    state               TEXT NOT NULL CHECK (state IN ('open', 'closed', 'merged')),
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    base_commit         TEXT NOT NULL,
    head_commit         TEXT NOT NULL,
    code_changes        JSONB NOT NULL,
    source_url          TEXT,
    PRIMARY KEY (source_instance, repository, pr_number, version_number),
    CHECK (version_at >= created_at),
    CHECK (jsonb_typeof(code_changes) = 'array')
);

CREATE TABLE pr_reviews (
    source_instance     TEXT NOT NULL,
    repository          TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    entry_type          TEXT NOT NULL CHECK (entry_type IN ('comment', 'approved', 'changes_requested', 'commented')),
    source_id           TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    pr_version_number   INTEGER NOT NULL,
    reviewed_commit     TEXT,
    author_source_id    TEXT,
    author_name         TEXT,
    body                TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    reply_to_source_id  TEXT,
    review_group_id     TEXT,
    file_path           TEXT,
    line_number         INTEGER CHECK (line_number > 0),
    diff_side           TEXT CHECK (diff_side IN ('before', 'after')),
    source_url          TEXT,
    PRIMARY KEY (source_instance, repository, pr_number, source_id, version_number),
    FOREIGN KEY (source_instance, repository, pr_number, pr_version_number)
        REFERENCES pr_versions (source_instance, repository, pr_number, version_number),
    CHECK (version_at >= created_at),
    CHECK (
        (line_number IS NULL AND diff_side IS NULL)
        OR
        (line_number IS NOT NULL AND diff_side IS NOT NULL AND file_path IS NOT NULL AND reviewed_commit IS NOT NULL)
    )
);

CREATE INDEX idx_mail_reply
    ON mail_messages (source_instance, in_reply_to_id);

CREATE INDEX idx_slack_thread
    ON slack_messages (source_instance, workspace_id, channel_id, thread_root_id, sent_at);

CREATE INDEX idx_issue_comments_issue
    ON issue_comments (source_instance, issue_id, created_at);

CREATE INDEX idx_pr_reviews_pr_version
    ON pr_reviews (source_instance, repository, pr_number, pr_version_number);
```
