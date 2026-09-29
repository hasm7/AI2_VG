# SQL Data Handoff: the Source Layer

This is the reference for the PostgreSQL source layer: what the six data sources are, how their ten tables are
built, every column, type, constraint and index, what the importer does with each field, and the rules the data must
follow so that every graph layer after it works. It is written so that someone who has never seen the project can
generate new, correct example data.

**Verified on 2026-09-29** directly against the live database (`psql`, read-only session), against
`scripts/setup_postgres_schema.py`, against the importer (`viewer/app.py`, `viewer/person_identity.py`) and against the
code of every graph layer that reads what the import writes. Nothing here is copied from older documents without being
checked.

Read next: `docs/DATA_GENERATION_GUIDE.md` (what the data must contain so that every layer has something to find),
`docs/GRAPH_DATA_HANDOFF.md` (what the rows become in Neo4j), `docs/PIPELINE_AND_LINKS_HANDOFF.md` (how everything
links together).

## Overview

### What the SQL layer is for

The project simulates the working material of one software team: mail with a customer, project chat, meeting
transcripts, tickets, requirement and design documents, and pull requests with reviews and code changes. PostgreSQL
stores that material **as the source systems would hold it**: one table group per source system, stable source IDs,
timestamps with time zones, and every earlier version of anything that can be edited. PostgreSQL is the source of
truth. The Neo4j graph is derived from it and can be rebuilt from it at any time.

The SQL layer deliberately does **not** contain links between sources. A Slack message does not have a foreign key to
an issue. Cross-source links exist only as **text**: a message that says `KV-7`, a document titled
`REQ-VAT: ...`, a PR description that says `kvitta-api#58`. Turning that text into relationships is the job of the
graph layers (section 8).

### Database facts

| Fact | Value |
| --- | --- |
| Server | PostgreSQL 18.6 (x86_64-windows) |
| Database | `hm_data` (default in code; read from `.env` `DB_NAME`) |
| Schema | `public`, 10 tables |
| Session time zone | `Europe/Berlin` (server default; timestamps are returned with the offset valid on that date, `+01:00` in winter, `+02:00` in summer) |
| Extensions | `plpgsql` only |
| Triggers, views, functions, sequences, enum types | none |
| Connection | `DATABASE_URL`, or `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` from `.env` |
| DDL source | `SCHEMA_SQL` in `scripts/setup_postgres_schema.py` (section 13). The live schema matches it exactly. |

`scripts/setup_postgres_schema.py` **drops all ten tables (`DROP TABLE ... CASCADE`) and recreates them empty**. It is
the way to start from a clean database, and it destroys all data. `scripts/migrate_issue_versioning.py` is a one-off,
idempotent migration that split `issues` out of `issue_versions`; on a schema created by the current setup script it
has nothing to do.

### The six data sources and their ten tables

| # | Logical source | Tables | `source_instance` used today | One row is |
| ---: | --- | --- | --- | --- |
| 1 | Mail | `mail_messages` | `kvitta-mail` | one email message |
| 2 | Slack / project chat | `slack_messages` | `kvitta-slack` | one **version** of one chat message |
| 3 | Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` | `kvitta-teams` | one meeting; one ordered transcript segment |
| 4 | Issues / tickets | `issues`, `issue_versions`, `issue_comments` | `kvitta-jira` | one issue identity; one **version** of the issue's state; one **version** of one comment |
| 5 | Requirements and technical documentation | `document_versions` | `kvitta-docs` | one **version** of one document |
| 6 | Pull requests, code reviews, code changes | `pr_versions`, `pr_reviews` | `kvitta-github` | one **version** of one PR (with its code changes as JSON); one **version** of one review entry |

The graph importer and every layer treat these six sources as separate trees. Some logical sources need more than one
table because the source system has parent and child objects (a meeting and its segments; an issue, its states and
its comments; a PR and its reviews).

### Foreign keys (the only enforced links)

```text
teams_meetings (source_instance, meeting_id)
    <- teams_transcript_segments (source_instance, meeting_id)

issues (source_instance, issue_id)
    <- issue_versions (source_instance, issue_id)
    <- issue_comments (source_instance, issue_id)

pr_versions (source_instance, repository, pr_number, version_number)
    <- pr_reviews (source_instance, repository, pr_number, pr_version_number)
```

Every other reference is **not** enforced by a foreign key and must be kept consistent by whoever writes the data:
`mail_messages.in_reply_to_id`, `slack_messages.thread_root_id`, `issue_comments.reply_to_comment_id`,
`pr_reviews.reply_to_source_id`, `pr_reviews.review_group_id`, `pr_reviews.reviewed_commit`, and all person fields.

**Insert order** that satisfies the foreign keys: `mail_messages`, `slack_messages`, `teams_meetings`,
`teams_transcript_segments`, `issues`, `issue_versions`, `issue_comments`, `document_versions`, `pr_versions`,
`pr_reviews`.

## Conventions that apply to every table

### IDs

- **`source_instance`** names the simulated source system instance. It is part of every primary key. Use one value
  per source system and keep it identical on every row of that system and its child tables (a segment's
  `source_instance` must equal its meeting's, a review's its PR's). Today: `kvitta-mail`, `kvitta-slack`,
  `kvitta-teams`, `kvitta-jira`, `kvitta-docs`, `kvitta-github`.
- **Stable object IDs** (`message_id`, `meeting_id`, `segment_id`, `issue_id`, `comment_id`, `document_id`,
  `source_id` of a review) never change between versions of the same object. Today they follow the pattern
  `<kind>-<zero-padded number>`: `mail-001`, `slack-006`, `meet-001`, `seg-003`, `issue-001`, `comment-002`,
  `doc-001`, `review-004`.
- **Zero-pad the numbers** (`comment-002`, not `comment-2`). The importer processes comments and reviews in text order
  of their ID, and a reply is only linked when its parent was processed first (section 9.3). Text order equals
  numeric order only with padding.
- **Human-readable keys** are separate from IDs where the source system has them: `issues.issue_key` (`KV-7`) next
  to `issue_id` (`issue-007`); `pr_versions.repository` + `pr_number` (`kvitta-api`, `58`, written `kvitta-api#58` in
  text). These keys are what people write in text, and what the reference layer matches (section 8).
- IDs are case-sensitive (`source_id` values are not lowercased by the identity resolver; emails are).

### Timestamps

- Every time column is `TIMESTAMPTZ` and every value must carry a time zone offset (`2026-03-03T14:25:00+01:00`).
- PostgreSQL stores the instant; on read it returns the value in the session time zone (`Europe/Berlin` on this
  server). The importer writes that ISO string into Neo4j (`isoformat()`), so the graph shows `+01:00`/`+02:00`.
- Keep times plausible and ordered: an answer cannot come before the question, a version cannot come before the one it
  replaces, a meeting segment offset must lie inside the meeting. Several checks enforce part of this (per table
  below); the rest is up to the data.
- Graph layers sort some timestamps **as text** (the Knowledge layer's evidence bundle, the Expertise layer's first and
  last activity). Mixed offsets (`+01:00` and `+02:00` across the daylight saving change) can reorder items that are
  less than an hour apart. Avoid putting related items within an hour of each other on either side of a DST change.

### Versions

- History is kept by **writing a new row** with the same stable ID and the next `version_number`. Rows are never
  updated in place.
- `version_number` starts at 1 and every table checks `version_number > 0`. Use consecutive numbers (1, 2, 3). The
  importer links versions as a chain `v1 -> v2 -> v3` by pairing neighbours in sorted order; a gap is tolerated but
  looks like a missing version.
- `version_at` is when that version was made. Where the table has a creation time (`created_at`, `sent_at`) there is a
  check `version_at >= created_at` (or `>= sent_at`). Keep `created_at` / `sent_at` **identical on every version** of
  the same object: it is the creation time of the object, not of the version. The importer takes it from the latest
  version.
- Tables with versions: `slack_messages`, `issue_versions`, `issue_comments`, `document_versions`, `pr_versions`,
  `pr_reviews`. Tables without: `mail_messages`, `teams_meetings`, `teams_transcript_segments`, `issues`.

### JSONB

Four fields are JSONB: `mail_messages.recipients`, `teams_meetings.participants`, `pr_versions.code_changes` (each has
a `CHECK (jsonb_typeof(...) = 'array')`), and none other. They must hold a real JSON **array** of objects, never a JSON
string containing JSON. Their exact object shapes are given per table.

### Person fields

Almost every table names people: senders, recipients, authors, speakers, creators, assignees, reviewers. Different
sources carry different identifiers (email only, source ID only, name only, or several). The importer merges them into
one `Person` per real person with fixed rules (section 7). **How the person fields are filled decides whether one
person becomes one node or three.** Read section 7 before generating data.

### Free text

`body`, `description`, `title`, `subject`, `acceptance_criteria`, `change_summary`, `before_summary`,
`after_summary` and review bodies are free text. They are where the story lives, and where cross-source references
are written (section 8). Write them as the real source would: short chat lines, formal ticket descriptions, Markdown
documents, concise review comments. English is used throughout today (the fulltext and reference patterns work on any
language, but the agent's keyword search translates questions into English keywords).

## 1. Mail: `mail_messages`

One row is one email message. Mail has no versions.

- **Primary key:** `mail_messages_pkey` `(source_instance, message_id)`
- **Checks:** `mail_messages_recipients_check` `CHECK (jsonb_typeof(recipients) = 'array')`
- **Index:** `idx_mail_reply` on `(source_instance, in_reply_to_id)`
- **Not null:** `source_instance`, `message_id`, `sender_address`, `recipients`, `body`, `sent_at`

| # | Column | Type | Null | Default | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | | Mail system instance | `MailMessage.source_instance` (key) |
| 2 | `message_id` | text | no | | Stable mail ID | `MailMessage.message_id` (key), also `name` and `display_name` |
| 3 | `sender_address` | text | no | | Sender email | `MailMessage.sender_address`; person observation (email + name) |
| 4 | `sender_name` | text | yes | | Sender display name | `MailMessage.sender_name`; person observation |
| 5 | `recipients` | jsonb | no | | Array of recipient objects (below) | `MailMessage.recipients_raw` (JSON string); one `MAIL_RECIPIENT` per resolvable recipient |
| 6 | `subject` | text | yes | | Subject line | `MailMessage.subject`; scanned for references |
| 7 | `body` | text | no | | Mail body, plain text with `\n` line breaks | `MailMessage.body`; scanned for references |
| 8 | `sent_at` | timestamptz | no | | Send time | `MailMessage.sent_at` (ISO string) |
| 9 | `in_reply_to_id` | text | yes | | `message_id` of the mail this replies to (same `source_instance`) | `MailMessage.in_reply_to_id` (property only, no relationship) |
| 10 | `source_url` | text | yes | | Link back to the source | `MailMessage.source_url` |

**`recipients` shape.** An array of objects:

```json
[
  {"name": "Kvitta Support", "type": "to", "address": "support@kvitta.se"},
  {"name": "Maria Lindgren", "type": "cc", "address": "maria.lindgren@kvitta.se"}
]
```

| Key | Required | Meaning |
| --- | --- | --- |
| `address` | yes (in practice) | Recipient email. Person observation (email). |
| `name` | recommended | Display name. Person observation (name). |
| `type` | recommended | `to`, `cc` or `bcc`. Stored on the relationship as `MAIL_RECIPIENT.recipient_type`; not validated. |

A recipient whose person cannot be resolved (no address and an ambiguous or unknown name) gets no relationship.

**Rules.**
- A reply sets `in_reply_to_id` to the parent's `message_id`. It is not a foreign key; the parent should exist and be
  earlier. There is no mail thread relationship in the graph, but the embedding text of a reply quotes the parent's
  subject (`In reply to: "..."`), found by matching `source_instance` and `message_id`.
- Replies typically keep the subject with `Re:` / `Fwd:`.
- Both sender and recipients become people. A shared mailbox (`support@...`) becomes a `Person` with
  `actor_type = "mailbox"` (section 7.5) and is left out of expertise, collaboration and embeddings.
- Each mail is its own collaboration work item: everyone who sent or received the same mail "works with" each other
  (see `docs/COLLABORATION_LAYER_HANDOFF.md`).

**Current rows (14),** `kvitta-mail`, 2026-02-09 to 2026-03-25, `source_url`
`https://mail.kvitta.se/thread/<first message of the thread>`. Five threads with the customer Anders Nyberg
(`anders.nyberg@bergstrom.se`, Bergström & Co), mostly to `support@kvitta.se` and answered by Maria Lindgren:

| Thread | Messages | Story |
| --- | --- | --- |
| VAT on restaurant receipts | `mail-001` Anders, `mail-002` Maria forwards to Sofia cc David (reply to 001), `mail-003` Maria answers (reply to 001), `mail-005` Anders confirms (reply to 003) | the VAT requirement change (`KV-4`, `REQ-VAT`) |
| Expenses not showing up in Fortnox | `mail-007` Anders, `mail-008` and `mail-009` Maria (name `KV-7`) | the Fortnox outage |
| Same expense paid twice | `mail-010` Anders, `mail-011` and `mail-013` Maria | the duplicate payout (`KV-8`) |
| How Kvitta stores your receipt images | `mail-012` David to Anders | image retention (`KV-6`) |
| Kvitta 1.0 - thank you | `mail-014` Anders to Maria cc David | after the release |

Plus `mail-004` (Maria to the whole team, time reporting; noise) and `mail-006` from the mailbox `alerts@kvitta.se`
(`[ALERT] Fortnox export failures`, to Ahmed and David). **`mail-006` and `mail-007` name no identifier**, because
they were written before `KV-7` existed; no layer links them to the outage, only search finds them.

**Example (`mail-008`, abbreviated body).**

```sql
INSERT INTO mail_messages (source_instance, message_id, sender_address, sender_name, recipients,
                           subject, body, sent_at, in_reply_to_id, source_url)
VALUES ('kvitta-mail', 'mail-008', 'maria.lindgren@kvitta.se', 'Maria Lindgren',
        '[{"name": "Anders Nyberg", "type": "to", "address": "anders.nyberg@bergstrom.se"},
          {"name": "Kvitta Support", "type": "cc", "address": "support@kvitta.se"}]'::jsonb,
        'Re: Expenses not showing up in Fortnox',
        'Hello Anders,

We know what is wrong. Fortnox changed how applications log in to their API, and our export still used the old way.
Nothing is lost: the expenses are queued and will be exported as soon as the fix is out. The work is tracked as KV-7,
and I will write again when it is fixed.

Best regards,
Maria Lindgren',
        '2026-03-03T10:05:00+01:00', 'mail-007', 'https://mail.kvitta.se/thread/mail-007');
```

## 2. Slack / project chat: `slack_messages`

One row is **one version** of one chat message. Editing a message adds a row with the same `message_id` and the next
`version_number`.

- **Primary key:** `slack_messages_pkey` `(source_instance, workspace_id, channel_id, message_id, version_number)`
- **Checks:** `slack_messages_version_number_check` `CHECK (version_number > 0)`;
  `slack_messages_check` `CHECK (version_at >= sent_at)`
- **Index:** `idx_slack_thread` on `(source_instance, workspace_id, channel_id, thread_root_id, sent_at)`
- **Not null:** `source_instance`, `message_id`, `version_number`, `workspace_id`, `channel_id`, `body`, `sent_at`,
  `version_at`

| # | Column | Type | Null | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | Chat system instance | key |
| 2 | `message_id` | text | no | Stable message ID, same on every version | key; also `name` and `display_name` (**without** the version) |
| 3 | `version_number` | integer | no | 1 = original, 2+ = edits | key |
| 4 | `workspace_id` | text | no | Workspace | key |
| 5 | `channel_id` | text | no | Channel ID | key |
| 6 | `channel_name` | text | yes | Human channel name, shown as `#channel_name` in the embedding text | property |
| 7 | `author_source_id` | text | yes | Author's chat user ID | property; person observation (source ID) |
| 8 | `author_name` | text | yes | Author display name | property; person observation (name) |
| 9 | `author_email` | text | yes | Author email | property; person observation (email) |
| 10 | `body` | text | no | Message text of this version | property; scanned for references |
| 11 | `sent_at` | timestamptz | no | When the message was first posted; identical on every version | property |
| 12 | `version_at` | timestamptz | no | When this version was written (= `sent_at` for version 1) | property |
| 13 | `thread_root_id` | text | yes | `message_id` of the thread's first message, for replies in a thread | property; `SLACK_THREAD_REPLY_TO` |
| 14 | `source_url` | text | yes | Link back | property |

**Rules.**
- Every version becomes its **own** `SlackMessage` node (the key includes `version_number`). The embedding layer
  groups them (`embedding_group` `Slack <message_id>`) and marks the highest version as latest.
- `sent_at` stays the same on all versions; `version_at` grows. The check only requires `version_at >= sent_at`.
- **Thread replies**: set `thread_root_id` to the root message's `message_id`, in the same `source_instance`,
  `workspace_id` and `channel_id`. The root itself has `thread_root_id` NULL (a root that points to itself is ignored).
  The importer links the reply to **every version** of the root, and only to root rows that already exist when the
  reply is imported; rows are imported ordered by `sent_at`, so the root must have an earlier `sent_at` than its
  replies. There is no nesting: a reply to a reply still points at the thread root.
- **Slack is the bridge for identity.** A chat row can carry `author_email` **and** `author_source_id` at once. That is
  what joins a person's email (seen in mail) to their source ID (seen in issues, documents, PRs, transcripts). Give
  every team member at least one chat row, or one meeting participant entry, with both (section 7).
- Chat is informal and short. It is where agreements, hand-offs and warnings are made, often naming an issue or PR.

**Current rows (50 rows, 47 messages).** `kvitta-slack`, workspace `w-kvitta`, 2026-02-02 to 2026-03-23,
`source_url` `https://kvitta.slack.com/archives/<channel_id>/<message_id>`. Five channels:

| `channel_id` | `channel_name` | Rows (messages) | Used for |
| --- | --- | ---: | --- |
| `c-dev` | `dev` | 16 (15) | most discussion, PRs, VAT decision |
| `c-mobile` | `mobile` | 11 (11) | the app and the offline debate |
| `c-incidents` | `incidents` | 14 (12) | the Fortnox outage and the duplicate payout |
| `c-releases` | `releases` | 5 (5) | release planning, go/no-go, release day |
| `c-random` | `random` | 4 (4) | noise (lunch, fika) |

Three messages are edited (two versions each): `slack-016` (Maria adds the VAT rates 6 minutes later), `slack-026`
(Ahmed adds "Tracking in KV-7" 80 minutes later, so version 1 names no issue) and `slack-042` (Nina, 7 minutes later).
26 messages are thread replies, to 13 roots (the largest thread is `slack-032`, the duplicate payout, with 6
replies). All seven team members write in Slack, every row with `author_source_id` **and** `author_email`; the
customer and the mailboxes never do.

**Example (an edited message, both versions).**

```sql
INSERT INTO slack_messages (source_instance, message_id, version_number, workspace_id, channel_id, channel_name,
                            author_source_id, author_name, author_email, body, sent_at, version_at,
                            thread_root_id, source_url)
VALUES
('kvitta-slack', 'slack-026', 1, 'w-kvitta', 'c-incidents', 'incidents', 'u-ahmed', 'Ahmed Karimi',
 'ahmed.karimi@kvitta.se',
 'Fortnox exports have failed since about 07:00, we get 401 Unauthorized from their API. Looking into it.',
 '2026-03-03T08:05:00+01:00', '2026-03-03T08:05:00+01:00', NULL,
 'https://kvitta.slack.com/archives/c-incidents/slack-026'),
('kvitta-slack', 'slack-026', 2, 'w-kvitta', 'c-incidents', 'incidents', 'u-ahmed', 'Ahmed Karimi',
 'ahmed.karimi@kvitta.se',
 'Fortnox exports have failed since about 07:00, we get 401 Unauthorized from their API. Looking into it. Tracking in KV-7.',
 '2026-03-03T08:05:00+01:00', '2026-03-03T09:25:00+01:00', NULL,
 'https://kvitta.slack.com/archives/c-incidents/slack-026');
```

A reply in that thread sets `thread_root_id = 'slack-026'` and is linked to both versions of the root.

## 3. Teams / meeting transcripts: `teams_meetings`, `teams_transcript_segments`

A meeting has metadata (one row in `teams_meetings`) and an ordered transcript (one row per spoken segment in
`teams_transcript_segments`). No versions.

### 3.1 `teams_meetings`

- **Primary key:** `teams_meetings_pkey` `(source_instance, meeting_id)`
- **Checks:** `teams_meetings_check` `CHECK (ended_at IS NULL OR ended_at >= started_at)`;
  `teams_meetings_participants_check` `CHECK (jsonb_typeof(participants) = 'array')`
- **Not null:** `source_instance`, `meeting_id`, `title`, `started_at`, `participants`

| # | Column | Type | Null | Default | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | | Meeting system instance | key |
| 2 | `meeting_id` | text | no | | Stable meeting ID | key; also `name`, `display_name` |
| 3 | `title` | text | no | | Meeting title | property; scanned for references; repeated at the top of every segment's embedding text |
| 4 | `started_at` | timestamptz | no | | Start time | property; also used as the time of every segment (segments have no time of their own) |
| 5 | `ended_at` | timestamptz | yes | | End time, `>= started_at` | property |
| 6 | `participants` | jsonb | no | `'[]'::jsonb` | Array of participant objects (below) | `participants_raw` (JSON string); one `PARTICIPATED_IN_MEETING` per resolvable participant |
| 7 | `source_url` | text | yes | | Link back | property |

**`participants` shape.**

```json
[
  {"name": "Maria Lindgren", "email": "maria.lindgren@kvitta.se", "source_id": "u-maria"},
  {"name": "Ahmed Karimi", "email": "ahmed.karimi@kvitta.se", "source_id": "u-ahmed"}
]
```

| Key | Accepted alternative | Meaning |
| --- | --- | --- |
| `name` | | Display name (person observation) |
| `email` | `address` | Email (person observation). The resolver reads `email`, else `address`. |
| `source_id` | `id` | Source ID (person observation). The resolver reads `source_id`, else `id`. |

Like Slack, a participant entry can carry email **and** source ID together, which bridges identities (section 7).

### 3.2 `teams_transcript_segments`

- **Primary key:** `teams_transcript_segments_pkey` `(source_instance, meeting_id, segment_id)`
- **Unique:** `teams_transcript_segments_source_instance_meeting_id_sequen_key`
  `(source_instance, meeting_id, sequence_number)`
- **Foreign key:** `teams_transcript_segments_source_instance_meeting_id_fkey` `(source_instance, meeting_id)`
  references `teams_meetings (source_instance, meeting_id)`
- **Checks:** `teams_transcript_segments_sequence_number_check` `CHECK (sequence_number > 0)`;
  `teams_transcript_segments_start_offset_ms_check` `CHECK (start_offset_ms >= 0)`;
  `teams_transcript_segments_check` `CHECK (end_offset_ms IS NULL OR end_offset_ms >= start_offset_ms)`
- **Not null:** `source_instance`, `meeting_id`, `segment_id`, `sequence_number`, `start_offset_ms`, `body`

| # | Column | Type | Null | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | Must equal the meeting's | key |
| 2 | `meeting_id` | text | no | Parent meeting | key; `HAS_TEAMS_TRANSCRIPT_SEGMENT` from the meeting |
| 3 | `segment_id` | text | no | Stable segment ID | key; also `name`, `display_name` |
| 4 | `sequence_number` | integer | no | Position in the meeting, 1, 2, 3 ... unique per meeting | property; the embedding text of segment n quotes segment n-1 (`Previous line (speaker): "..."`) |
| 5 | `speaker_source_id` | text | yes | Speaker's source ID | property; person observation (source ID) |
| 6 | `speaker_name` | text | yes | Speaker's name | property; person observation (name) |
| 7 | `start_offset_ms` | bigint | no | Milliseconds from meeting start | property |
| 8 | `end_offset_ms` | bigint | yes | Milliseconds from meeting start, `>= start_offset_ms` | property |
| 9 | `body` | text | no | What was said | property; scanned for references |

**Rules.**
- Segment IDs are unique **within a meeting** (the key includes `meeting_id`). Today they are unique across meetings
  too (`seg-001` ... `seg-045`); keep them unique across the whole instance, because the graph layers cite a segment
  by its bare `segment_id` (section 10).
- Number segments 1..n without gaps (the embedding text looks up exactly `sequence_number - 1`), and keep offsets
  increasing and inside the meeting's duration.
- A resolvable speaker gets `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` **and** `PARTICIPATED_IN_MEETING` (even if absent from
  `participants`).
- A speaker with `speaker_source_id` NULL and only a name is a name-only observation (section 7.3). If that name is
  shared by several people, the segment is linked to an ambiguous `Person`. Today every segment has a
  `speaker_source_id`, so there is no ambiguous person.
- **Meetings are where decisions happen, often without naming any identifier.** The Knowledge layer includes a whole
  meeting as soon as **one** of its segments mentions the issue it is working on (section 8 and
  `docs/KNOWLEDGE_LAYER_HANDOFF.md`). Make sure at least one segment of every relevant meeting names the issue key.

**Current rows (10 meetings, 45 segments).** `kvitta-teams`, `source_url` `https://teams.kvitta.se/meeting/<meeting_id>`.
Every participant entry carries name, email and source ID; every segment has a `speaker_source_id`.

| Meeting | Title | Start (end) | Participants | Segments |
| --- | --- | --- | ---: | --- |
| `meet-001` | Daily standup | 2026-02-03 09:15 (09:30) | 5 | `seg-001`..`seg-004` |
| `meet-002` | Offline design meeting (KV-2) | 2026-02-12 13:00 (13:45) | 5 | `seg-005`..`seg-011` |
| `meet-003` | VAT rules meeting (KV-4) | 2026-02-17 10:00 (10:30) | 3 | `seg-012`..`seg-015` |
| `meet-004` | Sprint review | 2026-02-20 14:00 (14:45) | 7 | `seg-016`..`seg-020` |
| `meet-005` | Daily standup | 2026-02-24 09:15 (09:30) | 6 | `seg-021`..`seg-023` |
| `meet-006` | Sprint review | 2026-03-06 14:00 (14:45) | 7 | `seg-024`..`seg-028` |
| `meet-007` | Release planning for Kvitta 1.0 (KV-9) | 2026-03-10 10:00 (10:45) | 6 | `seg-029`..`seg-033` |
| `meet-008` | Duplicate payout review (KV-8) | 2026-03-13 13:00 (13:40) | 5 | `seg-034`..`seg-038` |
| `meet-009` | Daily standup | 2026-03-17 09:15 (09:30) | 6 | `seg-039`..`seg-041` |
| `meet-010` | Go/no-go for Kvitta 1.0 (KV-9) | 2026-03-20 10:00 (10:30) | 7 | `seg-042`..`seg-045` |

Five meeting titles carry an issue key, which the reference layer scans. The decision about the offline queue is
made in `meet-002`; the sprint reviews and standups name issues only in some segments.

**Example (`meet-006` and its second segment).**

```sql
INSERT INTO teams_meetings (source_instance, meeting_id, title, started_at, ended_at, participants, source_url)
VALUES ('kvitta-teams', 'meet-006', 'Sprint review', '2026-03-06T14:00:00+01:00', '2026-03-06T14:45:00+01:00',
        '[{"name": "Maria Lindgren", "email": "maria.lindgren@kvitta.se", "source_id": "u-maria"},
          {"name": "David Okafor", "email": "david.okafor@kvitta.se", "source_id": "u-david"},
          {"name": "Sofia Berg", "email": "sofia.berg@kvitta.se", "source_id": "u-sofia"},
          {"name": "Ahmed Karimi", "email": "ahmed.karimi@kvitta.se", "source_id": "u-ahmed"},
          {"name": "Lucas Holm", "email": "lucas.holm@kvitta.se", "source_id": "u-lucas"},
          {"name": "Nina Petrova", "email": "nina.petrova@kvitta.se", "source_id": "u-nina"},
          {"name": "Emma Chen", "email": "emma.chen@kvitta.se", "source_id": "u-emma"}]'::jsonb,
        'https://teams.kvitta.se/meeting/meet-006');

INSERT INTO teams_transcript_segments (source_instance, meeting_id, segment_id, sequence_number, speaker_source_id,
                                       speaker_name, start_offset_ms, end_offset_ms, body)
VALUES ('kvitta-teams', 'meet-006', 'seg-025', 2, 'u-ahmed', 'Ahmed Karimi', 70000, 142000,
        'On Tuesday the Fortnox export stopped working, that is KV-7. Fortnox changed their login flow. A hotfix went out on Wednesday evening, and the proper fix is in review. I will write REVIEW-FORTNOX-OUTAGE next week.');
```

## 4. Issues / tickets: `issues`, `issue_versions`, `issue_comments`

An issue has a stable identity (`issues`, one row), a history of states (`issue_versions`, one row per version) and
comments (`issue_comments`, one row per version of each comment). The identity table exists because an issue's key,
creation time and creator never change, while title, description, acceptance criteria, status, priority and assignee
do.

### 4.1 `issues`

- **Primary key:** `issues_pkey` `(source_instance, issue_id)`
- **Unique:** `issues_source_instance_issue_key_key` `(source_instance, issue_key)`
- **Not null:** `source_instance`, `issue_id`, `issue_key`, `created_at`

| # | Column | Type | Null | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | Tracker instance | `Issue` key |
| 2 | `issue_id` | text | no | Stable internal ID (`issue-001`) | `Issue` key |
| 3 | `issue_key` | text | no | Human key written in text (`KV-7`) | `Issue.issue_key`, `name`, `display_name`; **the lookup target of every issue reference** |
| 4 | `created_at` | timestamptz | no | When the issue was created | `Issue.created_at` |
| 5 | `creator_source_id` | text | yes | Creator's source ID | `Issue.creator_source_id`; person observation; `CREATED_ISSUE` |
| 6 | `creator_name` | text | yes | Creator's name | `Issue.creator_name`; person observation |
| 7 | `source_url` | text | yes | Link to the issue | not used by the importer (the latest version's `source_url` is stored on `Issue`) |

**`issue_key` format.** It must match `\b[A-Z][A-Z0-9]*-\d+\b`: an uppercase project prefix, a hyphen, digits
(`KV-7`, `PAY-3`, `OPS2-120`). Anything else is never recognised in text, and no other source can link to the
issue. Keys should be unique across all `source_instance` values as well: the reference layer and the Knowledge layer
look issues up by `issue_key` alone.

### 4.2 `issue_versions`

- **Primary key:** `issue_versions_pkey` `(source_instance, issue_id, version_number)`
- **Unique:** `issue_versions_source_instance_issue_id_version_at_key` `(source_instance, issue_id, version_at)`, so two
  versions of one issue can never share a timestamp
- **Foreign key:** `issue_versions_source_instance_issue_id_fkey` `(source_instance, issue_id)` references
  `issues (source_instance, issue_id)`
- **Check:** `issue_versions_version_number_check` `CHECK (version_number > 0)`
- **Index:** `idx_issue_versions_history` on `(source_instance, issue_id, version_number)`
- **Not null:** `source_instance`, `issue_id`, `version_number`, `issue_type`, `title`, `status`, `version_at`

| # | Column | Type | Null | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | Must equal the issue's | `IssueVersion` key |
| 2 | `issue_id` | text | no | Parent issue | `IssueVersion` key; `HAS_ISSUE_VERSION` |
| 3 | `version_number` | integer | no | 1..n | `IssueVersion` key; `display_name` `<issue_key> v<n>`; `NEXT_ISSUE_VERSION` chain |
| 5 | `issue_type` | text | no | Free text; used: `story`, `bug`, `task`, `epic` | property |
| 6 | `title` | text | no | Title in this version | property; scanned |
| 7 | `description` | text | yes | Description in this version | property; scanned |
| 8 | `acceptance_criteria` | text | yes | Acceptance criteria in this version | property; scanned |
| 9 | `status` | text | no | Free text; used: `open`, `in progress`, `done` (`blocked` and others are fine) | property |
| 10 | `priority` | text | yes | Free text; used: `low`, `medium`, `high`, `critical` | property |
| 13 | `assignee_source_id` | text | yes | Assignee source ID | property; person observation |
| 14 | `assignee_name` | text | yes | Assignee name | property; person observation |
| 15 | `changed_by_id` | text | yes | Who made this change (source ID) | property; person observation; `CHANGED_ISSUE_VERSION` |
| 16 | `changed_by_name` | text | yes | Who made this change (name) | property; person observation |
| 18 | `version_at` | timestamptz | no | When this version was made | property |
| 19 | `source_url` | text | yes | Link | property |

The column positions have gaps (4, 11, 12, 17) because the migration dropped the columns that moved to `issues`
(`issue_key`, `created_at`, `creator_source_id`, `creator_name`). Always name the columns in `INSERT`.

**Rules.**
- Version 1 is the issue as created: `version_at` should equal `issues.created_at`, `changed_by` should be the
  creator. There is no check across the two tables; keep it consistent by hand.
- Each later version is a full snapshot of all fields after one change (status change, reassignment, rewritten
  description). Copy unchanged fields forward.
- The **latest** version (highest `version_number`) is copied onto the parent `Issue` node. The issue's owner in the
  graph (`OWNS_ISSUE`) is the latest version's assignee, or the creator when the latest assignee is NULL.
- Every version is embedded and searchable as its own node; a status history (`open -> in progress -> blocked -> in
  progress -> done`) is what lets questions like "why was it blocked, and when" be answered.

### 4.3 `issue_comments`

- **Primary key:** `issue_comments_pkey` `(source_instance, comment_id, version_number)`, so `comment_id` must be unique
  **per instance**, not only per issue
- **Foreign key:** `issue_comments_source_instance_issue_id_fkey` `(source_instance, issue_id)` references
  `issues (source_instance, issue_id)`
- **Checks:** `issue_comments_version_number_check` `CHECK (version_number > 0)`;
  `issue_comments_check` `CHECK (version_at >= created_at)`
- **Index:** `idx_issue_comments_issue` on `(source_instance, issue_id, created_at)`
- **Not null:** `source_instance`, `comment_id`, `version_number`, `issue_id`, `body`, `created_at`, `version_at`

| # | Column | Type | Null | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | Must equal the issue's | `IssueComment` key |
| 2 | `comment_id` | text | no | Stable comment ID | `IssueComment` key; `name`, `display_name` |
| 3 | `version_number` | integer | no | 1 = original, 2+ = edits | the **latest** version is imported; `version_count` records how many |
| 4 | `issue_id` | text | no | Issue commented on; same on every version | `HAS_ISSUE_COMMENT` from the issue |
| 5 | `author_source_id` | text | yes | Author source ID | person observation; `WROTE_ISSUE_COMMENT`, `COMMENTED_ON_ISSUE` |
| 6 | `author_name` | text | yes | Author name | person observation |
| 7 | `body` | text | no | Comment text | property; scanned |
| 8 | `created_at` | timestamptz | no | First posted; same on every version | property |
| 9 | `version_at` | timestamptz | no | This version | property |
| 10 | `reply_to_comment_id` | text | yes | `comment_id` of the comment this answers (same instance) | `REPLY_TO_ISSUE_COMMENT` |
| 11 | `source_url` | text | yes | Link | property |

**Rules.**
- Unlike Slack, only the **latest version** of a comment becomes a node. Earlier versions are kept in SQL but are not
  searchable in the graph. Use comment versions sparingly; edits matter more in Slack and documents.
- A reply's parent should have a **lower** `comment_id` (zero-padded) in the same issue, or be in an issue whose
  `issue_id` sorts earlier; otherwise the reply edge is not created on the first import (section 9.3).
- The importer processes comments per issue; a comment on `KV-8` that says `KV-2` (`comment-011`) becomes a
  cross-reference (`MENTIONS_ISSUE`), while a comment on `KV-9` that says `KV-9` would not (self-reference rule,
  section 8).

**Current rows (12 issues, 34 versions, 17 comments).** `kvitta-jira`, `source_url`
`https://jira.kvitta.se/browse/<issue_key>`, `issue-001`..`issue-012` = `KV-1`..`KV-12` in creation order.

| Key | Latest title | Type / priority | Created (by) | Status history (assignee) |
| --- | --- | --- | --- | --- |
| `KV-1` | Receipt reader: extract amount, VAT, date and merchant | story / medium | 02-02 Maria | open -> in progress (Sofia) x2 -> done |
| `KV-2` | Offline mode for expense capture | story / medium | 02-04 Maria | open -> in progress (Lucas) x2 -> done |
| `KV-3` | Onboarding: first tasks for Emma | task / low | 02-06 David | open (Emma) -> in progress -> done |
| `KV-4` | Support 12 % and 6 % VAT and representation rules | story / high | 02-10 Maria | open -> in progress (Sofia) -> done |
| `KV-5` | Approval limits per manager | story / medium | 02-16 Maria | open (Emma) -> in progress -> done |
| `KV-6` | Retention and access for receipt images | task / medium | 02-23 David | open -> in progress (Sofia) -> done |
| `KV-7` | Fortnox export failing after authentication change | bug / high | 03-03 Ahmed | open (Ahmed) -> in progress x2 -> done |
| `KV-8` | Duplicate payout of the same expense | bug / critical | 03-09 Maria | open -> in progress (Ahmed) -> done |
| `KV-9` | Release Kvitta 1.0 | epic / high | 03-09 Maria | open (Maria) -> in progress -> done |
| `KV-10` | Visma export | story / medium | 03-10 Maria | open |
| `KV-11` | Upload spinner never stops on slow network | bug / low | 03-11 Nina | open (Lucas) -> done |
| `KV-12` | Prevent duplicate submission from the mobile app | bug / medium | 03-17 Nina | open |

`KV-2` deliberately keeps its old description ("requires a network connection") in version 2 for four days after the
decision in `meet-002`; version 3 (2026-02-16) and `comment-005` correct it. Comments `comment-001`..`comment-017`,
one version each; one reply (`comment-014` replies to `comment-011`, both on `KV-8`).

**Example (`KV-8`: identity, two of its three versions, and the reply comment).**

```sql
INSERT INTO issues (source_instance, issue_id, issue_key, created_at, creator_source_id, creator_name, source_url)
VALUES ('kvitta-jira', 'issue-008', 'KV-8', '2026-03-09T10:00:00+01:00', 'u-maria', 'Maria Lindgren',
        'https://jira.kvitta.se/browse/KV-8');

INSERT INTO issue_versions (source_instance, issue_id, version_number, issue_type, title, description,
                            acceptance_criteria, status, priority, assignee_source_id, assignee_name,
                            changed_by_id, changed_by_name, version_at, source_url)
VALUES
('kvitta-jira', 'issue-008', 1, 'bug', 'Duplicate payout of the same expense',
 'Reported by Anders Nyberg at Bergström & Co on 9 March: a train ticket for 1 840 SEK was paid out twice in the March payroll. The expense appears twice in the payout file.',
 'The cause is found. A duplicate can never be paid out. The customer is informed and the extra payout is corrected.',
 'open', 'critical', NULL, NULL, 'u-maria', 'Maria Lindgren', '2026-03-09T10:00:00+01:00',
 'https://jira.kvitta.se/browse/KV-8'),
('kvitta-jira', 'issue-008', 2, 'bug', 'Duplicate payout of the same expense',
 'Reported by Anders Nyberg at Bergström & Co on 9 March: a train ticket for 1 840 SEK was paid out twice. Cause: the mobile app retried after a timeout, which is how the offline queue from KV-2 (kvitta-mobile#6, decided in ADR-OFFLINE-QUEUE) is designed to work. The first request had already arrived, and the payout export has no duplicate check, so both copies were paid. The risk was raised by Nina Petrova in her review of kvitta-mobile#6 in February.',
 'The cause is found. A duplicate can never be paid out. The customer is informed and the extra payout is corrected.',
 'in progress', 'critical', 'u-ahmed', 'Ahmed Karimi', 'u-ahmed', 'Ahmed Karimi', '2026-03-09T10:45:00+01:00',
 'https://jira.kvitta.se/browse/KV-8');

INSERT INTO issue_comments (source_instance, comment_id, version_number, issue_id, author_source_id, author_name,
                            body, created_at, version_at, reply_to_comment_id, source_url)
VALUES ('kvitta-jira', 'comment-014', 1, 'issue-008', 'u-nina', 'Nina Petrova',
        'The fix in kvitta-api#60 only guards the payout export. The app can still submit the same expense twice, so I opened KV-12 for 1.1.',
        '2026-03-17T09:10:00+01:00', '2026-03-17T09:10:00+01:00', 'comment-011', 'https://jira.kvitta.se/browse/KV-8');
```

## 5. Requirements and technical documentation: `document_versions`

One row is one version of one document. There is no separate identity table: all rows with the same
`(source_instance, document_id)` are one document.

- **Primary key:** `document_versions_pkey` `(source_instance, document_id, version_number)`
- **Checks:** `document_versions_version_number_check` `CHECK (version_number > 0)`;
  `document_versions_check` `CHECK (version_at >= created_at)`
- **Not null:** `source_instance`, `document_id`, `version_number`, `document_type`, `title`, `body`, `content_format`,
  `created_at`, `version_at`

| # | Column | Type | Null | Default | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | | Docs system instance | key |
| 2 | `document_id` | text | no | | Stable document ID (`doc-001`) | `Document`/`DocumentVersion` key; `Document.name`/`display_name`; version `display_name` `<document_id> v<n>` |
| 3 | `version_number` | integer | no | | 1..n | key; `NEXT_DOCUMENT_VERSION` chain |
| 4 | `document_type` | text | no | | Free text; used: `requirement`, `technical-design`, `decision`, `checklist`, `incident-review`. **`technical-design` has a special role** (below) | property |
| 5 | `title` | text | no | | Title, **with the document identifier first** (below) | property; scanned; source of the document identifier |
| 6 | `body` | text | no | | Document text, Markdown | property; scanned |
| 7 | `content_format` | text | no | `'markdown'` | Format of `body`; only `markdown` is used | property |
| 8 | `author_source_id` | text | yes | | Author of this version (source ID) | person observation; `AUTHORED_DOCUMENT_VERSION` |
| 9 | `author_name` | text | yes | | Author of this version (name) | person observation |
| 10 | `created_at` | timestamptz | no | | When the document was created; same on every version | property |
| 11 | `version_at` | timestamptz | no | | When this version was published | property |
| 12 | `change_summary` | text | yes | | What changed in this version and why | property; scanned; first-class line in the embedding text |
| 13 | `source_url` | text | yes | | Link | property |

**The document identifier (important).** A document can only be referenced from other text when its title starts with
an identifier followed by a colon: `REQ-VAT: VAT on expenses`. The reference layer takes the part before the first
`:`, trims it, and accepts it only if it matches `^[A-Z][A-Z0-9-]{3,}$` (uppercase letter, then at least three more
uppercase letters, digits or hyphens). `REQ-VAT`, `ADR-OFFLINE-QUEUE`, `DESIGN-RECEIPT-READER`, `RELEASE-CHECKLIST`,
`ADR-0007` work. A title without that prefix (`Receipt reader`) can never be referenced by text; such a document is
only reached through its author, through `technical-design` inclusion in the Architecture layer, or by search. All six
current documents have an identifier. Other text then refers to the document by writing the identifier exactly
(`REQ-VAT`, optionally followed by a version such as `REQ-VAT v2`). The identifier is taken from the
**latest** version's title.

**Rules.**
- Keep `document_id`, `document_type` and `created_at` the same on all versions; the title may change but should keep
  the identifier.
- Every version is embedded as its own `DocumentVersion` node; `change_summary` is the most important field for
  "how did the requirement change" questions. Write it on every version after the first.
- `document_type = 'technical-design'` documents are included in the Architecture layer's evidence for **every**
  repository (see `docs/ARCHITECTURE_LAYER_HANDOFF.md`). Use that type for design documents that describe code
  structure and components.
- **The latest version's author must resolve to a person.** If `author_source_id` and `author_name` of the highest
  version are both empty or unresolvable, the importer writes the `Document` node but **stops before creating any
  `DocumentVersion` node** (`import_document_row` returns early). The document then has no searchable versions and no
  version history. Always fill the author on every version.
- Markdown headings (`## Background`) are fine; the embedding text strips the `#` markers.

**Current rows (6 documents, 10 versions).** `kvitta-docs`, `source_url` `https://docs.kvitta.se/<document_id>`:

| Document | Identifier and title | Type | Author | Versions |
| --- | --- | --- | --- | --- |
| `doc-001` | `REQ-VAT: VAT on expenses` | requirement | Maria Lindgren | v1 02-02 (25 % only), v2 02-17 (25, 12 and 6 % and representation, after the customer's mail) |
| `doc-002` | `DESIGN-RECEIPT-READER: Receipt reader` | technical-design | Sofia Berg | v1 02-05 (14 257 characters), v2 02-18 (15 015 characters, VAT section rewritten) |
| `doc-003` | `ADR-OFFLINE-QUEUE: Local queue with automatic retry in the mobile app` | decision | David Okafor | v1 02-12 |
| `doc-004` | `ADR-IMAGE-RETENTION: Retention and access for receipt images` | decision | David Okafor | v1 02-27 |
| `doc-005` | `RELEASE-CHECKLIST: Kvitta 1.0` | checklist | Maria Lindgren | v1 03-09, v2 03-10 (Visma moved to 1.1), v3 03-20 (all checked) |
| `doc-006` | `REVIEW-FORTNOX-OUTAGE: Fortnox export outage 3-4 March` | incident-review | Ahmed Karimi | v1 03-11 |

`doc-002` is the only text over 12 000 characters, so it is the one the embedding layer splits into chunks. Version 1
of every document has `change_summary` `Initial ...`.

**Example (`doc-005` version 2).**

```sql
INSERT INTO document_versions (source_instance, document_id, version_number, document_type, title, body,
                               content_format, author_source_id, author_name, created_at, version_at,
                               change_summary, source_url)
VALUES ('kvitta-docs', 'doc-005', 2, 'checklist', 'RELEASE-CHECKLIST: Kvitta 1.0',
        '# RELEASE-CHECKLIST: Kvitta 1.0

Release date: Monday 23 March 2026. Tracked in KV-9.

## Features

- [x] Receipt reader with amount, VAT, date and merchant (KV-1)
- [x] VAT rates 25, 12 and 6 % and representation (KV-4)
- [x] Offline queue in the app (KV-2)
- [x] Approval limits per manager (KV-5)
- [x] Retention and access for receipt images (KV-6)
- [x] Fortnox export

Moved to 1.1 at release planning on 10 March: the Visma export (KV-10). The pilot customer uses Fortnox.

## Blockers

- [ ] Duplicate payout fixed (KV-8)
- [x] Proper Fortnox fix with token refresh (KV-7)

## Quality

- [ ] Full regression run on the release candidate
- [x] Receipt reader quality run on 500 receipts (96 % fully correct)

## Go-live

- [ ] Go/no-go meeting on 20 March
- [ ] Customer information to Bergström & Co',
        'markdown', 'u-maria', 'Maria Lindgren', '2026-03-09T14:05:00+01:00', '2026-03-10T15:30:00+01:00',
        'After release planning: Visma export moved to 1.1 (KV-10); KV-7 and KV-8 listed as blockers; proper Fortnox fix done.',
        'https://docs.kvitta.se/doc-005');
```

## 6. Pull requests, code reviews and code changes: `pr_versions`, `pr_reviews`

A pull request is versioned: each push or state change is a new row in `pr_versions`, and each row carries the code
changes of that version as a JSON array. Reviews, review decisions and line comments are rows in `pr_reviews`, each
tied to the PR version it was written against.

### 6.1 `pr_versions`

- **Primary key:** `pr_versions_pkey` `(source_instance, repository, pr_number, version_number)`
- **Checks:** `pr_versions_pr_number_check` `CHECK (pr_number > 0)`;
  `pr_versions_version_number_check` `CHECK (version_number > 0)`;
  `pr_versions_state_check` `CHECK (state IN ('open', 'closed', 'merged'))`;
  `pr_versions_check` `CHECK (version_at >= created_at)`;
  `pr_versions_code_changes_check` `CHECK (jsonb_typeof(code_changes) = 'array')`
- **Not null:** `source_instance`, `repository`, `pr_number`, `version_number`, `title`, `state`, `created_at`,
  `version_at`, `base_commit`, `head_commit`, `code_changes`

| # | Column | Type | Null | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | Code host instance | `PullRequest`/`CodeChange` key |
| 2 | `repository` | text | no | Repository name, **lowercase** (below) | key; `Repository` in the Architecture layer |
| 3 | `pr_number` | integer | no | PR number, `> 0` | key; `display_name` `<repository>#<pr_number>` |
| 4 | `version_number` | integer | no | 1..n | `PullRequest.version_number` = latest; part of the `CodeChange` key |
| 5 | `title` | text | no | Title in this version | latest on `PullRequest`; scanned |
| 6 | `description` | text | yes | Description in this version | latest on `PullRequest`; scanned |
| 7 | `author_source_id` | text | yes | PR author (source ID) | person observation; `AUTHORED_PR` (latest version's author) |
| 8 | `author_name` | text | yes | PR author (name) | person observation |
| 9 | `state` | text | no | `open`, `closed` or `merged` | latest on `PullRequest` |
| 10 | `created_at` | timestamptz | no | When the PR was opened; same on every version | property |
| 11 | `version_at` | timestamptz | no | When this version was pushed | property |
| 12 | `base_commit` | text | no | Commit the PR is based on | property |
| 13 | `head_commit` | text | no | Head commit of this version; reviews cite it in `reviewed_commit` | property |
| 14 | `code_changes` | jsonb | no | Array of changed files in this version (below) | one `CodeChange` node per element, per version |
| 15 | `source_url` | text | yes | Link | property |

**`repository` and the PR reference format.** Text refers to a PR as `<repository>#<number>`, matched by
`\b([a-z0-9][a-z0-9._-]*)#(\d+)\b`: the repository part must be **lowercase** letters, digits, `.`, `_`, `-`, starting
with a letter or digit (`kvitta-api#58`, `kvitta-mobile#6`, `web.client#12`). A repository named `Kvitta-API` can
never be referenced. The lookup is by `(repository, pr_number)` without `source_instance`, so keep repository names unique
across code hosts.

**`code_changes` shape.** An array with one object per changed file in this version:

```json
[
  {
    "file_path": "app/payouts/export.py",
    "change_type": "modified",
    "before_summary": "The payout export put every approved expense in the payout file without checking whether it had been paid before.",
    "after_summary": "The payout export checks a unique expense id and skips expenses that were already paid out. Expenses themselves can still be stored twice.",
    "diff": "@@ -22,6 +22,9 @@\n def build_payout_file(expenses):\n-    rows = [payout_row(e) for e in expenses]\n+    paid = paid_expense_ids()\n+    rows = [payout_row(e) for e in expenses if e.expense_uid not in paid]\n     return write_file(rows)"
  }
]
```

| Key | Required | Meaning | Used by |
| --- | --- | --- | --- |
| `file_path` | **yes** | Repository-relative path with `/` separators, no leading `/` (`app/payouts/export.py`). An element without it is skipped. | `CodeChange` key; Architecture layer: `File`, and `Module` = everything before the last `/` (`(root)` for a top-level file) |
| `change_type` | recommended | `added`, `modified`, `deleted`, `renamed` (free text; `added` and `modified` are used today) | property |
| `before_summary` | recommended | One sentence: what the code did before this change | property; **scanned for references**; embedding text |
| `after_summary` | recommended | One sentence: what it does after | property; **scanned for references**; embedding text |
| `diff` | recommended | Unified diff excerpt | property; LLM layers (first 4000 characters); embedding text. **Not** scanned for references. |

Notes on `code_changes`:
- **Each version lists the files changed in that version**, not the whole PR's cumulative diff. The same file changed
  in versions 1 and 3 gives two `CodeChange` nodes (the key includes `version_number`); `File.change_count` counts them.
- A file path must not appear twice in one version's array (both would merge into one node, the last one winning).
- In the current data the diffs use the JSON escape `\n`, so the stored text has real line breaks. A diff that
  instead contains the two characters `\` `n` (JSON `\\n`) also works: the embedding layer converts that sequence to
  a line break.
- The `before_summary` / `after_summary` pair is what makes "what did the PR claim vs what did the code do" questions
  answerable. Write them concretely, naming behaviour, not just "updated file".
- File paths are the only raw material for the Architecture layer: modules come from directories, components are
  proposed from files that belong together. Use a realistic directory layout and reuse the same paths across PRs.

**Rules.**
- `created_at`, `repository`, `pr_number` identical on all versions; `version_at` increasing; `state` usually `open`
  until the last version (`merged` or `closed`).
- `head_commit` changes with every version; `base_commit` usually stays. Reviews reference a version's `head_commit`.
- The parent `PullRequest` node carries the **latest** version's title, description, state, author and
  `code_changes_raw`. `CodeChange` nodes are created for **every** version.
- A PR with an empty `code_changes` array in every version produces no `Repository`, `Module` or `File`.
- A version that only changes state (the merge) can have an empty array `[]` and keep the previous `head_commit`. In
  the current data every final `merged` version is written that way.

### 6.2 `pr_reviews`

One row is one version of one review entry: an overall review decision, a general comment, or a comment on a specific
line.

- **Primary key:** `pr_reviews_pkey` `(source_instance, repository, pr_number, source_id, version_number)`
- **Foreign key:** `pr_reviews_source_instance_repository_pr_number_pr_version_fkey`
  `(source_instance, repository, pr_number, pr_version_number)` references
  `pr_versions (source_instance, repository, pr_number, version_number)`, so the PR version reviewed must exist
- **Checks:**
  - `pr_reviews_entry_type_check` `CHECK (entry_type IN ('comment', 'approved', 'changes_requested', 'commented'))`
  - `pr_reviews_version_number_check` `CHECK (version_number > 0)`
  - `pr_reviews_line_number_check` `CHECK (line_number > 0)`
  - `pr_reviews_diff_side_check` `CHECK (diff_side IN ('before', 'after'))`
  - `pr_reviews_check` `CHECK (version_at >= created_at)`
  - `pr_reviews_check1` `CHECK ((line_number IS NULL AND diff_side IS NULL) OR (line_number IS NOT NULL AND diff_side
    IS NOT NULL AND file_path IS NOT NULL AND reviewed_commit IS NOT NULL))`
- **Index:** `idx_pr_reviews_pr_version` on `(source_instance, repository, pr_number, pr_version_number)`
- **Not null:** `source_instance`, `repository`, `pr_number`, `entry_type`, `source_id`, `version_number`,
  `pr_version_number`, `created_at`, `version_at`

| # | Column | Type | Null | Meaning | In the graph |
| ---: | --- | --- | --- | --- | --- |
| 1 | `source_instance` | text | no | Must equal the PR's | `PullRequestReview` key |
| 2 | `repository` | text | no | PR's repository | key |
| 3 | `pr_number` | integer | no | PR number | key; `HAS_PR_REVIEW` from the PR |
| 4 | `entry_type` | text | no | `changes_requested`, `approved`, `comment` (used); `commented` (allowed, unused) | property; embedding line `Type: ...` |
| 5 | `source_id` | text | no | Stable ID of the entry (`review-001`) | key; `display_name` `<repository>#<pr_number> <source_id>` |
| 6 | `version_number` | integer | no | 1 = original, 2+ = edits | the **latest** version is imported; `version_count` records how many |
| 7 | `pr_version_number` | integer | no | Which PR version this was written against | property |
| 8 | `reviewed_commit` | text | yes | `head_commit` of that PR version; required for line comments | property |
| 9 | `author_source_id` | text | yes | Reviewer (source ID) | person observation; `WROTE_PR_REVIEW`, `REVIEWED_PR` |
| 10 | `author_name` | text | yes | Reviewer (name) | person observation |
| 11 | `body` | text | yes | Review text | property; scanned |
| 12 | `created_at` | timestamptz | no | First written; same on every version | property |
| 13 | `version_at` | timestamptz | no | This version | property |
| 14 | `reply_to_source_id` | text | yes | `source_id` of the entry this answers, **in the same PR** | `REPLY_TO_PR_REVIEW` |
| 15 | `review_group_id` | text | yes | Groups the entries submitted together as one review (`rg-001`) | property only |
| 16 | `file_path` | text | yes | File of a line comment; must be a path in that PR version's `code_changes` | property; embedding line `Location: ...` |
| 17 | `line_number` | integer | yes | Line of a line comment, `> 0` | property |
| 18 | `diff_side` | text | yes | `before` or `after` | property |
| 19 | `source_url` | text | yes | Link | property |

**Two kinds of entries.**
- **Whole-PR entry** (a decision or a general comment): `line_number` and `diff_side` NULL; `file_path` may be NULL.
- **Line comment**: `line_number`, `diff_side`, `file_path` **and** `reviewed_commit` all set (the check enforces all
  four together).

**Rules.**
- Every reviewer, including the PR author replying to a comment, gets `REVIEWED_PR` to the PR. For collaboration, a PR
  is a work item shared by its author and everyone with a review entry on it.
- `reviewed_commit` should equal the `head_commit` of `pr_version_number`'s row.
- A reply's parent must be in the same PR and should have a lower `source_id` (zero-padded), or the reply edge is
  missed on the first import (section 9.3).
- A realistic review flow: `changes_requested` on an early version with line comments, the author replying, a later
  version, then `approved` entries against the final version.

**Current rows (14 PRs, 38 versions, 39 code-change entries, 33 review entries).** `kvitta-github`, `source_url`
`https://github.kvitta.se/<repository>/pull/<pr_number>` (reviews add `#<source_id>`). Three repositories:
`kvitta-api` (17 distinct files), `kvitta-mobile` (4), `kvitta-web` (2). Every PR ends `merged`; states used are
`open` and `merged`.

| PR | Title | Author | Versions | Reviewers |
| --- | --- | --- | ---: | --- |
| `kvitta-api#12` | KV-1: receipt reader for amount, date and merchant | Sofia | 3 | David, Nina (Sofia replies) |
| `kvitta-api#21` | KV-2: accept expenses from the offline queue | Ahmed | 3 | David |
| `kvitta-api#22` | KV-4: VAT rates 25, 12 and 6 % in the receipt reader | Sofia | 3 | David (Sofia replies) |
| `kvitta-api#31` | KV-5: approval limits per manager | Emma | 4 | Ahmed (changes requested), Nina (Emma replies) |
| `kvitta-api#55` | KV-7: hotfix for the new Fortnox token flow | Ahmed | 2 | David |
| `kvitta-api#58` | KV-7: Fortnox client with the new token flow and automatic refresh | Ahmed | 3 | Sofia, David (Ahmed replies) |
| `kvitta-api#59` | KV-6: seven-year retention and finance-only access for receipt images | Sofia | 2 | David |
| `kvitta-api#60` | KV-8: idempotent payout export | Ahmed | 3 | Nina, David (Ahmed replies) |
| `kvitta-api#61` | KV-7 follow-up: log Fortnox request ids | Ahmed | 3 | Emma |
| `kvitta-mobile#6` | KV-2: offline queue with automatic retry | Lucas | 3 | Nina, David (Lucas replies) |
| `kvitta-mobile#9` | KV-11: stop the upload spinner when the upload finishes | Lucas | 2 | Nina |
| `kvitta-web#3` | KV-3: clearer wording on the approval button | Emma | 3 | Ahmed |
| `kvitta-web#5` | KV-3: show the receipt date in the expense list | Emma | 2 | Ahmed |
| `kvitta-web#9` | KV-5: show approval limits in the web portal | Emma | 2 | Maria, Ahmed |

Two reviews carry the story: `review-008` (Nina on `kvitta-mobile#6`, line comment `src/offline/retry.ts:4`, asks
whether the same expense can be sent twice; Lucas answers in `review-009` that it will be handled later) and
`review-030` (Nina on `kvitta-api#60`: the description says "prevents all duplicate expenses", but only the payout
export is guarded). Six entries are replies (`review-004`, `-009`, `-013`, `-017`, `-024`, `-031`).

**Example (`kvitta-api#60` version 1 and Nina's line comment).**

```sql
INSERT INTO pr_versions (source_instance, repository, pr_number, version_number, title, description,
                         author_source_id, author_name, state, created_at, version_at, base_commit, head_commit,
                         code_changes, source_url)
VALUES ('kvitta-github', 'kvitta-api', 60, 1, 'KV-8: idempotent payout export',
        'Prevents all duplicate expenses. The payout export checks a unique expense id and skips expenses that were already paid out, and a new test makes sure the same expense is never paid twice. Fixes KV-8.',
        'u-ahmed', 'Ahmed Karimi', 'open', '2026-03-12T15:15:00+01:00', '2026-03-12T15:15:00+01:00',
        '7e283411', 'a15b6744',
        '[{"file_path": "app/payouts/export.py", "change_type": "modified",
           "before_summary": "The payout export put every approved expense in the payout file without checking whether it had been paid before.",
           "after_summary": "The payout export checks a unique expense id and skips expenses that were already paid out. Expenses themselves can still be stored twice.",
           "diff": "@@ -22,6 +22,9 @@\n def build_payout_file(expenses):\n-    rows = [payout_row(e) for e in expenses]\n+    paid = paid_expense_ids()\n+    rows = [payout_row(e) for e in expenses if e.expense_uid not in paid]\n     return write_file(rows)"},
          {"file_path": "tests/payouts/test_export.py", "change_type": "added",
           "before_summary": "No test for duplicate payouts.",
           "after_summary": "Test that the same expense received twice is paid out only once.",
           "diff": "@@ -0,0 +1,29 @@\n+def test_same_expense_is_paid_once():\n+    rows = build_payout_file([train_ticket, train_ticket_copy])\n+    assert len(rows) == 1"}]'::jsonb,
        'https://github.kvitta.se/kvitta-api/pull/60');

INSERT INTO pr_reviews (source_instance, repository, pr_number, entry_type, source_id, version_number,
                        pr_version_number, reviewed_commit, author_source_id, author_name, body, created_at,
                        version_at, reply_to_source_id, review_group_id, file_path, line_number, diff_side, source_url)
VALUES ('kvitta-github', 'kvitta-api', 60, 'comment', 'review-030', 1, 1, 'a15b6744', 'u-nina', 'Nina Petrova',
        'This guards the payout export only. The description says it prevents all duplicate expenses, but the app can still submit the same expense twice after a timeout (see KV-2 and ADR-OFFLINE-QUEUE), and the duplicate expense is still stored. The payout is safe, the duplicate itself is not prevented.',
        '2026-03-12T16:00:00+01:00', '2026-03-12T16:00:00+01:00', NULL, 'rg-029',
        'app/payouts/export.py', 24, 'after', 'https://github.kvitta.se/kvitta-api/pull/60#review-030');
```

## 7. Person identity: how person fields become `Person` nodes

Implemented in `viewer/person_identity.py`. Every import and the Knowledge layer build the same registry from **all
ten tables** before writing anything, so identity does not depend on which source is imported first.

### 7.1 Where people are observed

| Table (origin) | Email | Source ID | Name |
| --- | --- | --- | --- |
| `mail_messages` sender | `sender_address` | | `sender_name` |
| `mail_messages.recipients[]` | `address` | | `name` |
| `slack_messages` author | `author_email` | `author_source_id` | `author_name` |
| `teams_meetings.participants[]` | `email` (or `address`) | `source_id` (or `id`) | `name` |
| `teams_transcript_segments` speaker | | `speaker_source_id` | `speaker_name` |
| `issues` creator | | `creator_source_id` | `creator_name` |
| `issue_versions` assignee | | `assignee_source_id` | `assignee_name` |
| `issue_versions` changed by | | `changed_by_id` | `changed_by_name` |
| `issue_comments` author | | `author_source_id` | `author_name` |
| `document_versions` author | | `author_source_id` | `author_name` |
| `pr_versions` author | | `author_source_id` | `author_name` |
| `pr_reviews` author | | `author_source_id` | `author_name` |

Only mail, Slack and Teams participants carry emails. Only Slack and Teams participants carry **both** an email and a
source ID. Issues, documents and PRs carry source ID and name only.

Normalisation: emails are trimmed and lowercased; source IDs are trimmed (case kept); names are trimmed, inner
whitespace collapsed and lowercased for comparison (the display keeps the original casing). An observation with no
email, no source ID and no name is ignored.

### 7.2 Clustering rules (union-find)

- **Rule A:** observations with the same email are the same person.
- **Rule B:** observations with the same source ID are the same person. `SOURCE_ID_IS_GLOBAL = True`: a source ID is
  treated as one person **across all source instances**. So `u-maria` in Slack, Jira, Teams, docs and GitHub is one
  person. **Give each person one source ID and use it in every system**, and never reuse a source ID for two people.
- **Rule C** follows from A and B: a Slack row or Teams participant with email **and** source ID joins the email
  cluster (from mail) with the source-ID cluster (from issues, PRs, documents).
- **Rule D (name-only observations):** an observation with a name but no email and no source ID attaches to an
  established person only when **exactly one** established person (one with an email or source ID) carries that
  normalised name. If several do, the name-only observations form their own person marked
  `identity_ambiguous = true`. If none does, they form their own weak person. A name never merges two established
  people.

"Carries that name" means any name observed in that person's cluster. Example: if Maria's cluster contained both
`Maria Lindgren` and `Maria` (a mail sent with display name `Maria`), and another person's cluster also contained
`Maria`, a transcript speaker `Maria` with no source ID would match two people and become the ambiguous person
`name:maria`. The current data avoids this on purpose: every team member has a distinct first name, every person
field carries the full name, and every speaker and participant has a source ID. There is no ambiguous and no weak
person today.

### 7.3 The resulting `Person` node

| Property | Rule |
| --- | --- |
| `person_key` | `email:<first email alphabetically>` if the cluster has an email, else `source:<first source ID>`, else `name:<first normalised name>` |
| `name` | The longest display name; ties prefer one with capital letters, then alphabetical |
| `email`, `emails` | First email; all emails (sorted) |
| `source_id`, `source_ids` | First source ID; all (sorted) |
| `names` | All display names (sorted) |
| `identity_confidence` | `strong` with an email or source ID, else `weak` |
| `identity_ambiguous` | `true` only for a name-only cluster whose name matched several people |
| `actor_type` | `mailbox` if **every** email's local part is in the mailbox list (7.5), else `person` |

### 7.4 How a source row finds its person

`resolve_person_key(email, source_id, name)` tries, in order: exact email; exact source ID; the name, if it belongs to
a weak name-only cluster; the name, if exactly one person carries it. Otherwise no person, and **no relationship is
written** for that row (the row's node is still created). The importer passes, per source: mail sender and recipients
email + name; Slack email + source ID + name; Teams participants email + source ID + name; segment speaker source ID +
name; all issue, document and PR fields source ID + name.

The Knowledge layer resolves the actor names the model writes into events **by name only**. Use full, distinct names
(`Maria Lindgren`, `Anders Nyberg`); a first name shared by two people resolves to the ambiguous person or to nobody.

### 7.5 Mailboxes

An email whose local part (before `@`) is exactly one of `noreply`, `no-reply`, `donotreply`, `do-not-reply`,
`support`, `info`, `hello`, `contact`, `admin`, `team`, `help`, `sales`, `billing`, `notifications`, `jira`, `github`,
`builds`, `ci`, `alerts`, `postmaster`, `mailer-daemon` is a mailbox. A person all of whose emails are mailboxes gets
`actor_type = "mailbox"`. Mailboxes and ambiguous persons are **excluded** from expertise, collaboration, graph
algorithms and embeddings (they still get their source relationships).

### 7.6 Current people

| `person_key` | Name | Emails | Source IDs | Names seen | Type |
| --- | --- | --- | --- | --- | --- |
| `email:maria.lindgren@kvitta.se` | Maria Lindgren | maria.lindgren@kvitta.se | u-maria | Maria Lindgren | person, strong (product owner) |
| `email:david.okafor@kvitta.se` | David Okafor | david.okafor@kvitta.se | u-david | David Okafor | person, strong (tech lead) |
| `email:sofia.berg@kvitta.se` | Sofia Berg | sofia.berg@kvitta.se | u-sofia | Sofia Berg | person, strong (backend developer) |
| `email:ahmed.karimi@kvitta.se` | Ahmed Karimi | ahmed.karimi@kvitta.se | u-ahmed | Ahmed Karimi | person, strong (integration developer) |
| `email:lucas.holm@kvitta.se` | Lucas Holm | lucas.holm@kvitta.se | u-lucas | Lucas Holm | person, strong (mobile developer) |
| `email:nina.petrova@kvitta.se` | Nina Petrova | nina.petrova@kvitta.se | u-nina | Nina Petrova | person, strong (tester) |
| `email:emma.chen@kvitta.se` | Emma Chen | emma.chen@kvitta.se | u-emma | Emma Chen | person, strong (new developer from 9 February) |
| `email:anders.nyberg@bergstrom.se` | Anders Nyberg | anders.nyberg@bergstrom.se | | Anders Nyberg | person, strong (customer, Bergström & Co; mail only) |
| `email:support@kvitta.se` | Kvitta Support | support@kvitta.se | | Kvitta Support | **mailbox** |
| `email:alerts@kvitta.se` | Kvitta Alerts | alerts@kvitta.se | | Kvitta Alerts | **mailbox** |

8 eligible persons, 2 excluded (both mailboxes). Each team member's email and source ID are joined through their
Slack rows and meeting participant entries (rule C).

## 8. Cross-source references in text

The sources are linked only through identifiers written in text. The reference layer (`backend/reference_extraction.py`,
full description in `docs/REFERENCE_EXTRACTION_LAYER_HANDOFF.md`) turns them into `MENTIONS_ISSUE`,
`MENTIONS_PULL_REQUEST` and `MENTIONS_DOCUMENT` relationships, and almost every later layer builds on those.

| Referenced thing | Write it as | Pattern | Must exist as |
| --- | --- | --- | --- |
| Issue | `KV-7` | `\b[A-Z][A-Z0-9]*-\d+\b` | `issues.issue_key` |
| Pull request | `kvitta-api#58` | `\b([a-z0-9][a-z0-9._-]*)#(\d+)\b` | `pr_versions.repository` + `pr_number` |
| Document | `REQ-VAT` | the exact identifier, whole word | leading token before `:` of the latest `document_versions.title`, matching `^[A-Z][A-Z0-9-]{3,}$` |

- Matches are case-sensitive and whole-word. A match that resolves to nothing is discarded (`UTF-8` looks like an issue
  key and is dropped).
- Document identifiers are matched first; their text is then consumed, so `REQ-VAT` is not also tried as an issue
  key. Avoid document identifiers that end in `-<digits>` and equal an issue key.
- **Scanned fields:** mail `subject`, `body`; Slack `body`; Teams segment `body` and meeting `title`; issue `title`,
  `description` (parent issue = latest version); issue version `title`, `description`, `acceptance_criteria`; comment
  `body`; document `title`, `body` (parent = latest) and document version `title`, `body`, `change_summary`; PR `title`,
  `description` (latest version only); review `body`; code change `before_summary`, `after_summary`.
- **Not scanned:** `diff`, `source_url`, names, JSON participant/recipient data, and every earlier PR version's title
  and description (only the latest reaches the `PullRequest` node).
- **Self-references are skipped:** a version, comment, review or code change naming its own parent issue, document or
  PR gets no edge. A comment on `KV-8` naming `KV-2` does.

Every relationship after this point depends on these references: the Knowledge layer builds its evidence for an issue
from the nodes that name the issue (or that the issue names), the Architecture layer finds messages about a
repository through the PRs they name, and so on (`docs/PIPELINE_AND_LINKS_HANDOFF.md`). **A source that names nothing
is invisible to every layer except search.** Write references the way people do: a PR title starting with its issue
key (`KV-8: idempotent payout export`), a chat line "Hotfix kvitta-api#55 is deployed", a comment "see
ADR-IMAGE-RETENTION", an issue description "Delivered in kvitta-mobile#6". In the current data the alert mail
`mail-006` and the customer's first Fortnox mail `mail-007` name nothing, on purpose.

## 9. The import: how rows reach Neo4j

### 9.1 How to run it

The import lives in the SQL viewer (`viewer/app.py`, Flask on port 5000). Start it with the `Open SQL Viewer` button in
the app (it calls `POST /api/viewer/start`, which starts `viewer/app.py` as a child process of the backend) or with
`.\scripts\run_viewer.ps1`. The viewer has one page per source (Mail, Slack, Teams, Issues, Documents, PRs) and on each
an import button that imports **all rows of that source**:

| Button (source page) | Route | Reads |
| --- | --- | --- |
| Importera alla mail | `POST /import/mail` | `mail_messages` |
| Importera alla Slack-meddelanden | `POST /import/slack` | `slack_messages` |
| Importera alla Teams-transkript | `POST /import/teams` | `teams_meetings`, then `teams_transcript_segments` |
| Importera alla ärenden | `POST /import/issues` | `issue_versions` joined with `issues`, and `issue_comments` |
| Importera alla dokument | `POST /import/documents` | `document_versions` |
| Importera alla PR:er | `POST /import/prs` | `pr_versions`, `pr_reviews` |

Each import: builds the person registry from all tables, writes every `Person` node, merges superseded person nodes
into their canonical node, creates its constraints if missing, writes its nodes and relationships with `MERGE`, and
sets `PipelineState.last_import_at`. The six imports can run in any order; run all six.

### 9.2 The import is additive

Every write is a `MERGE` plus `SET`. **Nothing is ever deleted by an import.** Rows removed or renamed in SQL leave
their old nodes in Neo4j; changed rows overwrite properties of the same node. To load a new dataset that replaces the
current one, the graph must be emptied first (for example `MATCH (n) DETACH DELETE n` in Neo4j Browser, which also
removes `PipelineState` and every derived layer; constraints and indexes stay). That is a destructive step and a
decision for the user. Then import all six sources and build every layer in order
(`docs/PIPELINE_AND_LINKS_HANDOFF.md`).

### 9.3 Import order inside a source, and what it means for the data

| Source | Processed in order of | Consequence |
| --- | --- | --- |
| Slack | `sent_at`, then IDs and `version_number` | A thread reply is linked to root rows that already exist: the root must be sent earlier. |
| Teams | meetings by `started_at`; segments by `meeting_id`, `sequence_number` | none |
| Issues | issues by `source_instance`, `issue_id`; comments grouped by `comment_id` in text order | `REPLY_TO_ISSUE_COMMENT` is written only if the parent comment node already exists: parent with a lower `comment_id` in the same issue, or in an issue imported earlier. |
| Documents | `document_id`, `version_number` | The latest version's author must resolve, or no `DocumentVersion` nodes are written (section 5). |
| PRs | `repository`, `pr_number`, `version_number`; reviews grouped by `source_id` in text order | `REPLY_TO_PR_REVIEW` only if the parent review (same PR) was processed first: lower `source_id`. |

A second import of the same source fixes a missed reply edge, since the parent then exists. Correct IDs make that
unnecessary.

### 9.4 What each table becomes

| Table | Nodes | Relationships |
| --- | --- | --- |
| `mail_messages` | `MailMessage` | `(Person)-[:SENT_MAIL]->(MailMessage)`, `(MailMessage)-[:MAIL_RECIPIENT {recipient_type}]->(Person)` |
| `slack_messages` | `SlackMessage` (one per version) | `SENT_SLACK_MESSAGE`, `(reply)-[:SLACK_THREAD_REPLY_TO]->(root, every version)` |
| `teams_meetings` | `TeamsMeeting` | `PARTICIPATED_IN_MEETING` |
| `teams_transcript_segments` | `TeamsTranscriptSegment` | `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT`, and `PARTICIPATED_IN_MEETING` for the speaker |
| `issues` + latest `issue_versions` | `Issue` (identity + latest state, `versions_raw`, `comments_raw`, counts) | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE` |
| `issue_versions` | `IssueVersion` (every version) | `HAS_ISSUE_VERSION`, `NEXT_ISSUE_VERSION`, `CHANGED_ISSUE_VERSION` |
| `issue_comments` (latest version) | `IssueComment` | `HAS_ISSUE_COMMENT`, `WROTE_ISSUE_COMMENT`, `REPLY_TO_ISSUE_COMMENT` |
| `document_versions` (latest) | `Document` (`versions_raw` holds the **earlier** versions) | `AUTHORED_DOCUMENT` |
| `document_versions` | `DocumentVersion` (every version) | `HAS_DOCUMENT_VERSION`, `NEXT_DOCUMENT_VERSION`, `AUTHORED_DOCUMENT_VERSION` |
| `pr_versions` (latest) | `PullRequest` (`code_changes_raw` of the latest version, `reviews_raw`) | `AUTHORED_PR`, `REVIEWED_PR` |
| `pr_versions.code_changes[]` (every version) | `CodeChange` | `HAS_CODE_CHANGE` |
| `pr_reviews` (latest version) | `PullRequestReview` | `HAS_PR_REVIEW`, `WROTE_PR_REVIEW`, `REPLY_TO_PR_REVIEW` |

Every property, key and constraint is listed in `docs/GRAPH_DATA_HANDOFF.md`.

## 10. Identifiers the graph layers use to cite evidence

The LLM layers show the model each source item under a short identifier and accept only those identifiers back. They
are derived from the SQL IDs, so SQL IDs must be unique enough for them to be unambiguous **across the whole dataset**:

| Item | Identifier | Uniqueness needed |
| --- | --- | --- |
| Issue | `issue_key` | unique across everything |
| Issue version | `<issue_key> v<n>` | follows |
| Issue comment | `comment_id` | unique across all instances |
| Mail | `message_id` | unique across mail and every other ID |
| Slack message | `<message_id> v<n>` | `message_id` unique across channels and workspaces |
| Teams meeting | `meeting_id` | unique |
| Transcript segment | `segment_id` | **unique across meetings** (not only within one) |
| Document | `document_id` | unique |
| Document version | `<document_id> v<n>` | follows |
| Pull request | `<repository>#<pr_number>` | follows |
| Review | `source_id` | **unique across all PRs** (not only within one) |
| Code change | `<repository>#<pr_number> <file_path> v<n>` (Knowledge layer: without ` v<n>`) | follows |

If two items share an identifier (two meetings both with a `seg-001`, two PRs both with a `review-001`), the layers
keep only one of them as evidence. Use prefixes that are unique across the dataset: `mail-001`, `slack-001`,
`meet-001`, `seg-001`, `comment-001`, `doc-001`, `review-001`, and continue the numbering instead of restarting it per
parent.

## 11. Checklist for a new dataset

- [ ] Every primary key unique, every foreign key parent inserted first, every check satisfied (run the inserts in the
      order from the Overview; a failure names the constraint).
- [ ] One `source_instance` per source system, identical on child rows.
- [ ] IDs zero-padded and unique across the dataset (section 10).
- [ ] Issue keys uppercase `PROJ-123`; repositories lowercase; document titles `IDENT: Title` for every document that
      should be referenceable.
- [ ] Every team member has one source ID used everywhere, and at least one Slack row or Teams participant entry with
      that source ID **and** their email.
- [ ] Mailboxes use a mailbox local part; customers and outsiders have their own email domain.
- [ ] Versions numbered 1..n, `created_at`/`sent_at` identical on all versions, `version_at` increasing.
- [ ] Every document version has an author that resolves.
- [ ] Thread roots sent before replies; comment and review parents have lower IDs than their replies.
- [ ] Line comments have `file_path`, `line_number`, `diff_side` and `reviewed_commit`; `reviewed_commit` equals the
      reviewed version's `head_commit`.
- [ ] Every `code_changes` element has `file_path`; paths are realistic and reused.
- [ ] Text references are written where people would write them, so that every source links to at least one issue,
      PR or document (section 8 and `docs/DATA_GENERATION_GUIDE.md`).
- [ ] At least one segment of every relevant meeting names the issue it is about.
- [ ] JSONB fields are arrays of objects.

## 12. Current dataset in one paragraph

The **Kvitta** scenario, loaded from `data/kvitta_seed.sql` (one transaction, `SET client_encoding = 'UTF8'`). The
story in plain Swedish, the people and the demo questions are in the `kvitta/` folder.

263 rows in total: 14 mails, 50 Slack rows (47 messages), 10 meetings with 45 segments, 12 issues with 34 versions and
17 comments, 10 document versions (6 documents), 38 PR versions (14 PRs) with 39 code-change entries, 33 review
entries. From 2026-02-02 to 2026-03-25.

Kvitta AB, a team of seven, builds an expense app (receipt photo, reader, approval, export to accounting) for its
pilot customer Bergström & Co, contact Anders Nyberg. Nine storylines plus everyday noise:

1. The receipt reader is built (`KV-1`, `DESIGN-RECEIPT-READER`, `kvitta-api#12`).
2. The customer reports wrong VAT on restaurant receipts; `REQ-VAT` v2 adds 12 % and 6 % (`KV-4`, `kvitta-api#22`).
3. The offline debate: Lucas wants a local queue with automatic retry, Ahmed wants to require a network; David decides
   for the queue (`KV-2`, `meet-002`, `ADR-OFFLINE-QUEUE`, `kvitta-mobile#6`, `kvitta-api#21`). Nina warns in
   `review-008` that retries can send the same expense twice, and is told "later".
4. Fortnox changes its token flow and the export fails for about 38 hours (`KV-7`, hotfix `kvitta-api#55`, proper fix
   `kvitta-api#58`, follow-up `kvitta-api#61`, `REVIEW-FORTNOX-OUTAGE`).
5. A train ticket is paid out twice: caused by the retry from storyline 3 and a payout export without a duplicate
   check (`KV-8`, `meet-008`). `kvitta-api#60` claims to "prevent all duplicate expenses" but only guards the export;
   Nina points it out and opens `KV-12`.
6. Emma Chen joins on 9 February with Ahmed as mentor (`KV-3`, `kvitta-web#3`, `kvitta-web#5`).
7. Approval limits per manager (`KV-5`, `kvitta-api#31`, `kvitta-web#9`).
8. The Kvitta 1.0 release: planning, Visma export moved to 1.1 (`KV-10`), go/no-go on 20 March, release on 23 March
   (`KV-9`, `RELEASE-CHECKLIST`).
9. GDPR and receipt images: seven-year retention and finance-only access (`KV-6`, `ADR-IMAGE-RETENTION`,
   `kvitta-api#59`).

Deliberate contradictions: `KV-2` still says "requires a network connection" for four days after the decision, and
`kvitta-api#60` claims more than its code does. Three repositories, 23 files, 8 eligible persons and 2 mailboxes.

## 13. Authoritative DDL

`SCHEMA_SQL` from `scripts/setup_postgres_schema.py`, identical to the live schema (the live constraint names are
listed per table above):

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

CREATE TABLE issues (
    source_instance     TEXT NOT NULL,
    issue_id            TEXT NOT NULL,
    issue_key           TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    creator_source_id   TEXT,
    creator_name        TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, issue_id),
    UNIQUE (source_instance, issue_key)
);

CREATE TABLE issue_versions (
    source_instance     TEXT NOT NULL,
    issue_id            TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    issue_type          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT,
    acceptance_criteria TEXT,
    status              TEXT NOT NULL,
    priority            TEXT,
    assignee_source_id  TEXT,
    assignee_name       TEXT,
    changed_by_id       TEXT,
    changed_by_name     TEXT,
    version_at          TIMESTAMPTZ NOT NULL,
    source_url          TEXT,
    PRIMARY KEY (source_instance, issue_id, version_number),
    UNIQUE (source_instance, issue_id, version_at),
    FOREIGN KEY (source_instance, issue_id)
        REFERENCES issues (source_instance, issue_id)
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
        REFERENCES issues (source_instance, issue_id),
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

CREATE INDEX idx_issue_versions_history
    ON issue_versions (source_instance, issue_id, version_number);

CREATE INDEX idx_issue_comments_issue
    ON issue_comments (source_instance, issue_id, created_at);

CREATE INDEX idx_pr_reviews_pr_version
    ON pr_reviews (source_instance, repository, pr_number, pr_version_number);
```

If this document and the script ever disagree, the script and the live database win; update this document from them.
