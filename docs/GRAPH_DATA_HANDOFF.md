# Graph Database Handoff Specification

This document describes how the PostgreSQL source tables are imported into Neo4j and how the graph is exposed to the React frontend.

The SQL database is the source-preserving layer. Neo4j is the relationship and memory layer. The graph model intentionally keeps the six source groups visible through node labels and relationship types instead of flattening everything into one generic node model.

## Related Documents and Code

- Live schema snapshot for index/traversal planning (labels, properties, data types, existing constraints/indexes, relationship endpoint pairs, straight from a running Neo4j introspection): `GRAPH_SCHEMA_HANDOFF.md`
- SQL source schema: `docs/SQL_DATA_HANDOFF.md`
- PostgreSQL DDL: `scripts/setup_postgres_schema.py`
- SQL-to-Neo4j import code: `viewer/app.py`
- Person identity resolution: `viewer/person_identity.py`
- Derived reference layer (Task 05): `backend/reference_extraction.py`
- LLM-driven knowledge layer (Task 06): `backend/topic_event_extraction.py`
- Graph API used by frontend: `backend/app.py`
- Optional chat agent behind `/api/ai/chat`: `backend/langgraph_agent/agent.py`
- Frontend graph renderer: `frontend/src/main.tsx`

## Import Flow

Each source group is imported from the SQL viewer through source-specific import actions in `viewer/app.py`.

| Source group | SQL tables | Neo4j labels | Main relationships |
| --- | --- | --- | --- |
| Mail | `mail_messages` | `MailMessage`, `Person` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| Slack / project chat | `slack_messages` | `SlackMessage`, `Person` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` | `TeamsMeeting`, `TeamsTranscriptSegment`, `Person` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| Issues / tickets | `issues`, `issue_versions`, `issue_comments` | `Issue`, `IssueVersion`, `IssueComment`, `Person` | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE`, `HAS_ISSUE_VERSION`, `NEXT_ISSUE_VERSION`, `CHANGED_ISSUE_VERSION`, `HAS_ISSUE_COMMENT`, `WROTE_ISSUE_COMMENT`, `REPLY_TO_ISSUE_COMMENT` |
| Requirements and technical documentation | `document_versions` | `Document`, `DocumentVersion`, `Person` | `AUTHORED_DOCUMENT`, `HAS_DOCUMENT_VERSION`, `NEXT_DOCUMENT_VERSION`, `AUTHORED_DOCUMENT_VERSION` |
| Pull requests, code reviews, and code changes | `pr_versions`, `pr_reviews` | `PullRequest`, `PullRequestReview`, `CodeChange`, `Person` | `AUTHORED_PR`, `REVIEWED_PR`, `HAS_PR_REVIEW`, `WROTE_PR_REVIEW`, `REPLY_TO_PR_REVIEW`, `HAS_CODE_CHANGE` |

The imports are idempotent at the node/relationship level because they use `MERGE` with unique keys.

Every import first runs person identity resolution over **all** source tables, not only the tables of the source group being imported. `Person` nodes are written from the resolved identity clusters before any relationship is created, so all six source groups agree on the same `Person` nodes no matter which import runs.

## Graph Modeling Principles

Neo4j is a derived graph over the PostgreSQL source records. The import should be repeatable: rerunning an import uses `MERGE` and uniqueness constraints so the graph converges to the same model instead of accumulating duplicates.

The graph has two kinds of nodes:

- **Source object nodes** represent source-level objects such as `MailMessage`, `SlackMessage`, `TeamsMeeting`, `Issue`, `Document`, and `PullRequest`.
- **Retrieval-unit nodes** represent addressable pieces of source content that need their own identity for traversal, retrieval, citation, and later extraction: `IssueVersion`, `IssueComment`, `DocumentVersion`, `PullRequestReview`, and `CodeChange`.

Raw JSON/string properties are retained on parent nodes even when retrieval-unit nodes exist. This keeps backwards compatibility with the frontend and preserves a compact source summary, while the first-class nodes provide precise graph attachment points.

Relationship type names are source-filter-specific. Do not reuse a generic relationship type such as `HAS_VERSION` across source groups, because `backend/app.py` maps frontend filters by relationship type. Use source-specific names such as `HAS_ISSUE_VERSION` and `HAS_DOCUMENT_VERSION`.

`Person` nodes are shared across source groups and are written centrally from identity clusters before relationships are created. Relationship import paths should `MATCH` existing `Person` nodes by `person_key`, not `MERGE` ad hoc person nodes from individual rows.

## Shared `Person` Model

One `Person` node represents one real person across all six source groups.

Identity is resolved in `viewer/person_identity.py` as a two-phase step that runs before the relationships are written. It has to work this way because only mail, Slack and Teams participants carry an email address, while issues, documents, pull requests and transcript speakers carry only a source ID and a name. The join across sources is possible because two tables carry an email **and** a source ID in the same row:

- `slack_messages` has `author_email` and `author_source_id`
- `teams_meetings.participants` entries have `email` and `source_id`

Those rows are the bridges that let `u-anna` in Jira be recognised as `anna@example.com` in mail.

Neo4j label:

- `Person`

Unique constraint:

```cypher
CREATE CONSTRAINT person_key IF NOT EXISTS
FOR (p:Person)
REQUIRE p.person_key IS UNIQUE
```

### Phase 1: Collect Observations

Every person-bearing field in the SQL schema emits one observation with `email`, `source_id`, `name`, `origin` and `source_instance`. The fields are:

| Table | Person fields | Email present? |
| --- | --- | --- |
| `mail_messages` | `sender_address`, `sender_name` | yes |
| `mail_messages.recipients` (JSONB) | `address`, `name` per entry | yes |
| `slack_messages` | `author_source_id`, `author_name`, `author_email` | yes |
| `teams_meetings.participants` (JSONB) | `source_id`, `name`, `email` per entry | yes |
| `teams_transcript_segments` | `speaker_source_id`, `speaker_name` | no |
| `issues` | `creator_source_id`/`creator_name` | no |
| `issue_versions` | `assignee_source_id`/`assignee_name`, `changed_by_id`/`changed_by_name`, once per version | no |
| `issue_comments` | `author_source_id`, `author_name` | no |
| `document_versions` | `author_source_id`, `author_name` | no |
| `pr_versions` | `author_source_id`, `author_name` | no |
| `pr_reviews` | `author_source_id`, `author_name` | no |

Normalisation on the way in:

- email: stripped and lowercased
- name: stripped, internal whitespace collapsed, lowercased for comparison, original casing kept for display
- source_id: stripped but **not** lowercased, because source IDs can be case-sensitive
- empty strings are treated as missing

### Phase 2: Cluster Observations

Observations are clustered with union-find. The rules are applied strongest first:

- **Rule A - email.** Observations with the same normalised email are the same person. This is the strongest signal and is never overridden.
- **Rule B - source_id.** Observations with the same `source_id` are the same person.
- **Rule C - transitive bridging.** Rules A and B share one union-find structure, so a Slack row or a Teams participant entry holding both an email and a source ID automatically merges the email cluster with the source ID cluster. This is where the cross-source join happens.
- **Rule D - name, conditional.** An observation with only a name may be attached to an existing cluster **only if exactly one** cluster carries that normalised name. Zero matches create a new cluster. Two or more matches make the name ambiguous, and the observation gets a separate cluster that is marked as ambiguous. A name never merges two established identities.

`SOURCE_ID_IS_GLOBAL` in `viewer/person_identity.py` controls rule B. It is `True`, which means a `source_id` is treated as globally unique across source instances, because `docs/SQL_DATA_HANDOFF.md` tells data generators to reuse source IDs consistently. Setting it to `False` scopes rule B per `source_instance` if that assumption ever breaks.

### Phase 3: Canonical Key

Each cluster gets one `person_key` by this precedence:

1. `"email:" + <lowest-sorted email in the cluster>` when the cluster has any email
2. `"source:" + <lowest-sorted source_id>` when the cluster has any source ID
3. `"name:" + <normalised name>`

Sorting keeps the key deterministic when a cluster contains several emails, so repeated imports produce the same key.

### Phase 4: `Person` Properties

One node per cluster:

| Property | Meaning |
| --- | --- |
| `person_key` | Canonical key from phase 3. Same semantics as before, new derivation. |
| `name` | Best display name in the cluster: the longest original-casing name. |
| `email` | Primary email, the one used in the key, or null. |
| `source_id` | Primary source ID, or null. |
| `emails` | Sorted list of all emails in the cluster. |
| `source_ids` | Sorted list of all source IDs in the cluster. |
| `names` | Sorted list of all distinct display names seen. |
| `identity_confidence` | `"strong"` when the cluster was joined by email or source ID, `"weak"` when the cluster is name-only. |
| `identity_ambiguous` | `true` when rule D found more than one candidate cluster for the name, otherwise `false`. |
| `actor_type` | `"person"` or `"mailbox"`. A cluster with no email is `"person"`. A cluster with any human-looking email is `"person"`. It is `"mailbox"` only when every email local part is in the functional mailbox pattern list. |

The `emails`, `source_ids` and `names` lists are the lookup surface for later extraction steps that need to match a name mentioned in free text back to a person.

Functional mailbox classification is an exact, case-insensitive match on the email local part before `@`. The initial pattern list is: `noreply`, `no-reply`, `donotreply`, `do-not-reply`, `support`, `info`, `hello`, `contact`, `admin`, `team`, `help`, `sales`, `billing`, `notifications`, `jira`, `github`, `builds`, `ci`, `alerts`, `postmaster`, `mailer-daemon`. Substrings are not matched.

### Phase 5: Resolution at Relationship Time

Every import path resolves its person through one shared function:

```python
resolve_person_key(email=None, source_id=None, name=None) -> str | None
```

No `person_key` is built by string concatenation anywhere in the import path. Relationship creation only matches the already written `Person` node by its canonical key, so per-row values can no longer overwrite the resolved properties.

### Idempotency

The import uses **incremental merging**. Canonical `Person` nodes are written first, then any pre-existing `Person` node whose key is no longer canonical is resolved through the same function, has its relationships moved to the canonical node, and is deleted. Nothing is left orphaned, and running the import twice does not change the node or relationship count.

This matters because the canonical key can change when new data introduces an email for a person previously known only by name: the key moves from `name:...` to `email:...`, and the old node is merged into the new one. Each import result message reports the merges as `old_key -> new_key`.

## Neo4j Constraints

The importer creates these constraints as needed:

| Label | Constraint key |
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

## Source Models

### Mail

SQL source table:

- `mail_messages`

Neo4j node:

- `(:MailMessage)`

Unique key:

- `(source_instance, message_id)`

Properties copied to `MailMessage`:

| Property | SQL source |
| --- | --- |
| `source_instance` | `mail_messages.source_instance` |
| `message_id` | `mail_messages.message_id` |
| `name` | `message_id` |
| `display_name` | `message_id` |
| `sender_address` | `mail_messages.sender_address` |
| `sender_name` | `mail_messages.sender_name` |
| `recipients_raw` | JSON string from `mail_messages.recipients` |
| `subject` | `mail_messages.subject` |
| `body` | `mail_messages.body` |
| `sent_at` | ISO string from `mail_messages.sent_at` |
| `in_reply_to_id` | `mail_messages.in_reply_to_id` |
| `source_url` | `mail_messages.source_url` |

Relationships:

```cypher
(:Person)-[:SENT_MAIL]->(:MailMessage)
(:MailMessage)-[:MAIL_RECIPIENT {recipient_type}]->(:Person)
```

Notes:

- Sender and recipients are resolved through `resolve_person_key` from `sender_address`/`sender_name` and the `recipients` JSONB array.
- Mail reply chains are stored as `in_reply_to_id` on the `MailMessage` node, but there is currently no explicit Neo4j relationship for mail replies.

### Slack / Project Chat

SQL source table:

- `slack_messages`

Neo4j node:

- `(:SlackMessage)`

Unique key:

- `(source_instance, workspace_id, channel_id, message_id, version_number)`

Properties copied to `SlackMessage`:

| Property | SQL source |
| --- | --- |
| `source_instance` | `slack_messages.source_instance` |
| `workspace_id` | `slack_messages.workspace_id` |
| `channel_id` | `slack_messages.channel_id` |
| `message_id` | `slack_messages.message_id` |
| `version_number` | `slack_messages.version_number` |
| `channel_name` | `slack_messages.channel_name` |
| `display_name` | `message_id` |
| `name` | `message_id` |
| `author_source_id` | `slack_messages.author_source_id` |
| `author_name` | `slack_messages.author_name` |
| `author_email` | `slack_messages.author_email` |
| `body` | `slack_messages.body` |
| `sent_at` | ISO string from `slack_messages.sent_at` |
| `version_at` | ISO string from `slack_messages.version_at` |
| `thread_root_id` | `slack_messages.thread_root_id` |
| `source_url` | `slack_messages.source_url` |

Relationships:

```cypher
(:Person)-[:SENT_SLACK_MESSAGE]->(:SlackMessage)
(:SlackMessage)-[:SLACK_THREAD_REPLY_TO]->(:SlackMessage)
```

Notes:

- Each edited Slack message version becomes a separate `SlackMessage` node because `version_number` is part of the graph key.
- A thread reply points to the root message when `thread_root_id` exists and differs from `message_id`.

### Teams / Meeting Transcripts

SQL source tables:

- `teams_meetings`
- `teams_transcript_segments`

Neo4j nodes:

- `(:TeamsMeeting)`
- `(:TeamsTranscriptSegment)`

Unique keys:

- `TeamsMeeting`: `(source_instance, meeting_id)`
- `TeamsTranscriptSegment`: `(source_instance, meeting_id, segment_id)`

Properties copied to `TeamsMeeting`:

| Property | SQL source |
| --- | --- |
| `source_instance` | `teams_meetings.source_instance` |
| `meeting_id` | `teams_meetings.meeting_id` |
| `name` | `meeting_id` |
| `display_name` | `meeting_id` |
| `title` | `teams_meetings.title` |
| `started_at` | ISO string from `teams_meetings.started_at` |
| `ended_at` | ISO string from `teams_meetings.ended_at` when present |
| `participants_raw` | JSON string from `teams_meetings.participants` |
| `source_url` | `teams_meetings.source_url` |

Properties copied to `TeamsTranscriptSegment`:

| Property | SQL source |
| --- | --- |
| `source_instance` | `teams_transcript_segments.source_instance` |
| `meeting_id` | `teams_transcript_segments.meeting_id` |
| `segment_id` | `teams_transcript_segments.segment_id` |
| `name` | `segment_id` |
| `display_name` | `segment_id` |
| `sequence_number` | `teams_transcript_segments.sequence_number` |
| `speaker_source_id` | `teams_transcript_segments.speaker_source_id` |
| `speaker_name` | `teams_transcript_segments.speaker_name` |
| `start_offset_ms` | `teams_transcript_segments.start_offset_ms` |
| `end_offset_ms` | `teams_transcript_segments.end_offset_ms` |
| `body` | `teams_transcript_segments.body` |

Relationships:

```cypher
(:Person)-[:PARTICIPATED_IN_MEETING]->(:TeamsMeeting)
(:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(:TeamsTranscriptSegment)
(:Person)-[:SPOKE_TEAMS_TRANSCRIPT_SEGMENT]->(:TeamsTranscriptSegment)
```

Notes:

- Meeting participants come from the `participants` JSONB array.
- A transcript speaker also gets `PARTICIPATED_IN_MEETING` to the parent meeting.
- Segments are ordered by `sequence_number` in SQL, but there is currently no explicit `NEXT_SEGMENT` relationship in Neo4j.

### Issues / Tickets

SQL source tables:

- `issues`
- `issue_versions`
- `issue_comments`

Neo4j node:

- `(:Issue)`

Unique key:

- `(source_instance, issue_id)`

Properties copied to `Issue`:

| Property | SQL source |
| --- | --- |
| `source_instance` | `issues.source_instance` |
| `issue_id` | `issues.issue_id` |
| `name` | `issue_key` |
| `display_name` | `issue_key` |
| `issue_key` | `issues.issue_key` |
| `issue_type` | latest `issue_versions.issue_type` |
| `title` | latest `issue_versions.title` |
| `description` | latest `issue_versions.description` |
| `acceptance_criteria` | latest `issue_versions.acceptance_criteria` |
| `status` | latest `issue_versions.status` |
| `priority` | latest `issue_versions.priority` |
| `creator_name` | `issues.creator_name` |
| `creator_source_id` | `issues.creator_source_id` |
| `assignee_name` | latest `issue_versions.assignee_name` |
| `assignee_source_id` | latest `issue_versions.assignee_source_id` |
| `created_at` | ISO string from `issues.created_at` |
| `version_at` | ISO string from latest `issue_versions.version_at` |
| `version_number` | latest `issue_versions.version_number` |
| `version_count` | Number of versions for the issue |
| `versions_raw` | JSON string of every version, ordered by `version_number` ascending |
| `comment_count` | Count of imported comments for the issue |
| `comments_raw` | JSON string built from `issue_comments` |
| `source_url` | latest `issue_versions.source_url` |

Relationships:

```cypher
(:Person)-[:CREATED_ISSUE]->(:Issue)
(:Person)-[:OWNS_ISSUE]->(:Issue)
(:Person)-[:COMMENTED_ON_ISSUE]->(:Issue)
(:Issue)-[:HAS_ISSUE_VERSION]->(:IssueVersion)
(:IssueVersion)-[:NEXT_ISSUE_VERSION]->(:IssueVersion)
(:Person)-[:CHANGED_ISSUE_VERSION]->(:IssueVersion)
(:Issue)-[:HAS_ISSUE_COMMENT]->(:IssueComment)
(:Person)-[:WROTE_ISSUE_COMMENT]->(:IssueComment)
(:IssueComment)-[:REPLY_TO_ISSUE_COMMENT]->(:IssueComment)
```

Notes:

- The graph keeps one `Issue` node per `(source_instance, issue_id)`. Identity comes from `issues`; the node's main state comes from the **latest** version, selected by the highest `version_number`. This matches how `Document` and `PullRequest` are imported.
- Every version is embedded in `Issue.versions_raw`, ordered by `version_number` ascending, with `version_count` holding how many there are. Each entry holds `version_number`, `version_at`, `status`, `issue_type`, `title`, `priority`, `assignee_name`, `assignee_source_id`, `changed_by_name` and `changed_by_id`.
- `IssueVersion` nodes are also created for every row in `issue_versions`. They carry the version fields including `description` and `acceptance_criteria`, which are not duplicated into `Issue.versions_raw`. Consecutive versions are linked by `NEXT_ISSUE_VERSION`.
- `IssueComment` nodes are created from the latest row for each `(source_instance, comment_id)`, with `version_count` preserving how many SQL versions exist for that comment.
- The raw JSON properties `Issue.versions_raw` and `Issue.comments_raw` are retained alongside the first-class nodes.
- `CREATED_ISSUE` comes from `issues.creator_source_id` and `issues.creator_name`, one relationship per row in `issues`. `OWNS_ISSUE` follows the assignee of the latest version, falling back to the creator when no assignee is set. The two are deliberately separate: creator and assignee are different roles and answer different questions, so later extraction does not have to infer one from version history.
- The owner person is the assignee when present, otherwise the creator.
- One `COMMENTED_ON_ISSUE` relationship is created per distinct comment author.

`IssueVersion` properties:

| Property | SQL source |
| --- | --- |
| `source_instance`, `issue_id`, `version_number`, `issue_type`, `title`, `description`, `acceptance_criteria`, `status`, `priority`, `assignee_source_id`, `assignee_name`, `changed_by_id`, `changed_by_name`, `source_url` | `issue_versions` |
| `version_at` | ISO string from `issue_versions.version_at` |
| `name`, `display_name` | `issue_key + " v" + version_number` |

`IssueComment` properties:

| Property | SQL source |
| --- | --- |
| `source_instance`, `comment_id`, `issue_id`, `author_source_id`, `author_name`, `body`, `version_number`, `reply_to_comment_id`, `source_url` | latest `issue_comments` row for the comment |
| `created_at`, `version_at` | ISO strings from latest `issue_comments` row |
| `version_count` | Number of SQL versions for the comment |
| `name`, `display_name` | `comment_id` |

### Requirements and Technical Documentation

SQL source table:

- `document_versions`

Neo4j node:

- `(:Document)`

Unique key:

- `(source_instance, document_id)`

Properties copied to `Document`:

| Property | SQL source |
| --- | --- |
| `source_instance` | `document_versions.source_instance` |
| `document_id` | `document_versions.document_id` |
| `name` | `document_id` |
| `display_name` | `document_id` |
| `document_type` | latest `document_versions.document_type` |
| `title` | latest `document_versions.title` |
| `body` | latest `document_versions.body` |
| `content_format` | latest `document_versions.content_format` |
| `author_name` | latest `document_versions.author_name` |
| `author_source_id` | latest `document_versions.author_source_id` |
| `created_at` | ISO string from latest `document_versions.created_at` |
| `version_at` | ISO string from latest `document_versions.version_at` |
| `version_number` | latest `document_versions.version_number` |
| `change_summary` | latest `document_versions.change_summary` |
| `versions_raw` | JSON string of earlier document versions |
| `source_url` | latest `document_versions.source_url` |

Relationship:

```cypher
(:Person)-[:AUTHORED_DOCUMENT]->(:Document)
(:Document)-[:HAS_DOCUMENT_VERSION]->(:DocumentVersion)
(:DocumentVersion)-[:NEXT_DOCUMENT_VERSION]->(:DocumentVersion)
(:Person)-[:AUTHORED_DOCUMENT_VERSION]->(:DocumentVersion)
```

Notes:

- The graph keeps one `Document` node per `(source_instance, document_id)`.
- The latest SQL version becomes the main node state.
- Earlier versions are embedded in `Document.versions_raw`.
- `DocumentVersion` nodes are created for every row in `document_versions`, including the latest. The raw JSON property is retained.

`DocumentVersion` properties:

| Property | SQL source |
| --- | --- |
| `source_instance`, `document_id`, `version_number`, `document_type`, `title`, `body`, `content_format`, `author_source_id`, `author_name`, `change_summary`, `source_url` | `document_versions` |
| `created_at`, `version_at` | ISO strings from `document_versions` |
| `name`, `display_name` | `document_id + " v" + version_number` |

### Pull Requests, Code Reviews, and Code Changes

SQL source tables:

- `pr_versions`
- `pr_reviews`

Neo4j node:

- `(:PullRequest)`

Unique key:

- `(source_instance, repository, pr_number)`

Properties copied to `PullRequest`:

| Property | SQL source |
| --- | --- |
| `source_instance` | `pr_versions.source_instance` |
| `repository` | `pr_versions.repository` |
| `pr_number` | `pr_versions.pr_number` |
| `name` | `repository + "#" + pr_number` |
| `display_name` | `repository + "#" + pr_number` |
| `title` | latest `pr_versions.title` |
| `description` | latest `pr_versions.description` |
| `author_name` | latest `pr_versions.author_name` |
| `author_source_id` | latest `pr_versions.author_source_id` |
| `state` | latest `pr_versions.state` |
| `created_at` | ISO string from latest `pr_versions.created_at` |
| `version_at` | ISO string from latest `pr_versions.version_at` |
| `version_number` | latest `pr_versions.version_number` |
| `base_commit` | latest `pr_versions.base_commit` |
| `head_commit` | latest `pr_versions.head_commit` |
| `code_changes_raw` | JSON string from latest `pr_versions.code_changes` |
| `reviews_raw` | JSON string built from latest review entries |
| `source_url` | latest `pr_versions.source_url` |

Relationships:

```cypher
(:Person)-[:AUTHORED_PR]->(:PullRequest)
(:Person)-[:REVIEWED_PR]->(:PullRequest)
(:PullRequest)-[:HAS_PR_REVIEW]->(:PullRequestReview)
(:Person)-[:WROTE_PR_REVIEW]->(:PullRequestReview)
(:PullRequestReview)-[:REPLY_TO_PR_REVIEW]->(:PullRequestReview)
(:PullRequest)-[:HAS_CODE_CHANGE]->(:CodeChange)
```

Notes:

- The graph keeps one `PullRequest` node per `(source_instance, repository, pr_number)`.
- The latest SQL PR version becomes the main node state.
- PR reviews are embedded as JSON in `PullRequest.reviews_raw`, and `PullRequestReview` nodes are created from the latest row for each `(source_instance, repository, pr_number, source_id)`.
- Code changes from the latest PR version are retained in `PullRequest.code_changes_raw`, and `CodeChange` nodes are created for every file entry in every PR version.
- The raw JSON properties `PullRequest.reviews_raw` and `PullRequest.code_changes_raw` are retained alongside the first-class nodes.
- One `REVIEWED_PR` relationship is created per distinct review author.

`PullRequestReview` properties:

| Property | SQL source |
| --- | --- |
| `source_instance`, `repository`, `pr_number`, `source_id`, `entry_type`, `version_number`, `pr_version_number`, `reviewed_commit`, `author_source_id`, `author_name`, `body`, `reply_to_source_id`, `review_group_id`, `file_path`, `line_number`, `diff_side`, `source_url` | latest `pr_reviews` row for the review entry |
| `created_at`, `version_at` | ISO strings from latest `pr_reviews` row |
| `version_count` | Number of SQL versions for the review entry |
| `name`, `display_name` | `repository + "#" + pr_number + " " + source_id` |

`CodeChange` properties:

| Property | SQL source |
| --- | --- |
| `source_instance`, `repository`, `pr_number`, `version_number` | Parent `pr_versions` row |
| `file_path`, `change_type`, `before_summary`, `after_summary`, `diff` | One object in `pr_versions.code_changes` |
| `name`, `display_name` | `repository + "#" + pr_number + " " + file_path` |

## Derived Reference Layer

Everything described above is a transcript of a SQL row. This layer is not: it is the first thing the graph concludes rather than copies.

The six source trees share only `Person`, so without this layer the only path from a mail to a pull request runs through a human being, and that path says nothing about content. But the content connections are already written down in the text. `AUTH-17` appears in Slack, in a transcript, in two document versions and in a pull request title. This pass turns those strings into relationships.

It is entirely mechanical: regular expressions and lookups, no model and no interpretation. It is implemented in `backend/reference_extraction.py` and runs from the "Build graph layers" tab in the frontend, never as part of an import.

### Relationship types

```cypher
(:SourceNode)-[:MENTIONS_ISSUE]->(:Issue)
(:SourceNode)-[:MENTIONS_PULL_REQUEST]->(:PullRequest)
(:SourceNode)-[:MENTIONS_DOCUMENT]->(:Document)
```

The target is always the parent entity, never a version. A text that says `AUTH-17` refers to the issue, not to one of its versions.

### Properties on every extracted relationship

| Property | Value |
| --- | --- |
| `derived` | `true` |
| `extracted_by` | `"reference-extraction-v1"` |
| `matched_text` | The exact substring that matched, for example `AUTH-17`. |
| `source_property` | The property it was found in, for example `body`. |
| `extracted_at` | ISO timestamp of the run. |

`derived: true` separates a conclusion from a copied fact, which any grounded answer needs to distinguish. `matched_text` and `source_property` are what let a human check an edge in one second.

### What is scanned

| Label | Properties |
| --- | --- |
| `MailMessage` | `subject`, `body` |
| `SlackMessage` | `body` |
| `TeamsTranscriptSegment` | `body` |
| `TeamsMeeting` | `title` |
| `Issue` | `title`, `description` |
| `IssueVersion` | `title`, `description`, `acceptance_criteria` |
| `IssueComment` | `body` |
| `Document` | `title`, `body` |
| `DocumentVersion` | `title`, `body`, `change_summary` |
| `PullRequest` | `title`, `description` |
| `PullRequestReview` | `body` |
| `CodeChange` | `before_summary`, `after_summary` |

`Person` is not scanned. The raw JSON properties (`versions_raw`, `comments_raw`, `reviews_raw`, `code_changes_raw`) are not scanned either: their content is already covered by the retrieval-unit nodes, and scanning both would double-count.

### Patterns and lookups

The lookup tables are built from the graph itself, never hardcoded. Whatever is in the graph is what can be referenced.

| Reference | Pattern | Lookup |
| --- | --- | --- |
| Issue key | `\b[A-Z][A-Z0-9]*-\d+\b` | every `Issue.issue_key` |
| Pull request | `\b([a-z0-9][a-z0-9._-]*)#(\d+)\b` | every `PullRequest` by `(repository, pr_number)` |
| Document | whole-word match on the identifier | the leading token of `Document.title` before the first colon, accepted only when it matches `^[A-Z][A-Z0-9-]{3,}$` |

**An unresolved match is discarded.** The issue-key pattern also matches strings such as `UTF-8`, and the lookup is what throws them away. That discard is the safety mechanism; the patterns are deliberately not made smarter.

Document identifiers are resolved first and their spans are consumed, so `REQ-AUTH-SESSION` cannot also be offered to the issue-key pattern. One string never produces two edges.

### Self-reference rule

A match is skipped when the target is the node's own parent entity. A comment on AUTH-17 whose body says `AUTH-17` gets no edge, because `HAS_ISSUE_COMMENT` already says it. A comment on AUTH-19 that says `AUTH-17` does get one, because that is a genuine cross-reference. The rule applies to `IssueVersion`, `DocumentVersion`, `PullRequestReview` and `CodeChange` against their parents, and to a parent node referencing itself.

### Re-runnability

Each run deletes every relationship where `extracted_by = "reference-extraction-v1"` and then rebuilds from scratch. Deletion is by that property only, never by relationship type, so a relationship created by an import is never touched. The pass creates no nodes and skips what it cannot resolve. Because the imports use `MERGE` and delete nothing, re-importing leaves these edges in place.

### `PipelineState`

One bookkeeping node, `(:PipelineState {id: "singleton"})`, carries two timestamps:

| Property | Written by | Meaning |
| --- | --- | --- |
| `last_import_at` | each of the six import paths | when source data last entered the graph |
| `last_extraction_at` | the extraction pass | when the reference layer was last rebuilt |

The UI shows a needs-re-run state when `last_import_at` is newer than `last_extraction_at`, or when no extraction has run. The node holds no data, is excluded from the graph API responses, and never appears in the visualisation.

## Interpreted Knowledge Layer

Everything above, including the derived reference layer, is either a transcript of a SQL row or a mechanical string match. Neither can see a sentence that carries meaning without naming an identifier. `seg-003` — Priya's rejection of the proposed fix — names nothing, and it is the reason `AUTH-17` was blocked. This layer exists to pick sentences like that one up.

It is implemented in `backend/topic_event_extraction.py` and runs from a second button in the same "Build graph layers" tab as the Task 05 panel, never as part of an import and never as part of the reference extraction pass.

### Model and configuration

| Constant | Value | Meaning |
| --- | --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra` | Chosen deliberately as a fixed baseline: this run should not itself be the uncertain variable that a cheaper model is later measured against. |
| `OPENAI_REASONING_EFFORT` | `medium` | |
| `EXTRACTION_VERSION` | `topic-event-extraction-v1` | Stamped as `generated_by` on every node and relationship this pass creates. |

The OpenAI Responses API is used with structured outputs (`client.responses.parse` against a Pydantic schema), never free text parsed as JSON. The API key comes from `OPENAI_API_KEY` in `.env` at runtime, never in code or in the frontend. When it is missing, `/api/knowledge/build` answers `503` with an explanatory message instead of a stack trace, the same contract `/api/ai/chat` already follows.

### One call per topic candidate

One topic candidate is one `Issue`. On the current data that is two calls: `AUTH-17` and `AUTH-19`.

For each issue, a bundle is assembled from:

- The issue itself, its `IssueVersion` history in order, and its `IssueComment`s.
- Every node reached from the issue, its versions, or its comments by a Task 05 reference edge (`extracted_by = "reference-extraction-v1"`), in either direction.
- **Sibling context**: when a `PullRequest` or `Document` enters the bundle this way, its `PullRequestReview`, `CodeChange`, or `DocumentVersion` children are pulled in too.
- **Meeting expansion**: when any `TeamsTranscriptSegment` enters the bundle, every segment of its parent `TeamsMeeting` is added, in `sequence_number` order, along with the meeting itself. A meeting is one conversation; half of it is worse than none, because the model infers the missing half.

Every bundle item carries a stable identifier built from the node's own properties (for example `AUTH-17 v3`, `seg-003`, `doc-001 v2`, `slack-006 v2`), which the model must quote back exactly when citing evidence. The bundle is sorted chronologically before it is sent.

Before each call, the model is given the slug and name of every `Topic` resolved so far in the current run (not read back from Neo4j, since prior runs of this pass are deleted at the start of every run — see re-runnability below). If the issue belongs to one of them, the model sets `existing_topic_slug`; otherwise it proposes a new topic. This is the same suggest/decide split as person resolution in Task 01: the model proposes, the code in section "Resolution layer" below decides.

### Resolution layer

Nothing the model returns reaches the graph without passing these checks, in order:

1. Every `evidence` identifier must be one from the bundle sent for that call. Anything else is discarded, and the discard count is reported.
2. An event with no surviving evidence after step 1 is dropped entirely.
3. A causal link is dropped if either of its events was dropped, or if it has no surviving evidence.
4. Self-causation (an event causing itself) is dropped.
5. `occurred_at` must parse as a timestamp, or the event is dropped.
6. Each `actor_names` entry is looked up through `person_identity.build_registry(...).resolve_person_key(name=...)`. A match creates `ACTED_IN_EVENT`; a miss is skipped silently — no `Person` node is ever created here.
7. At most 15 events and 15 causal links are kept per topic candidate; any excess is dropped and counted, as a guard against runaway output.

`existing_topic_slug` is accepted only when it matches a slug already resolved in this run exactly; anything else is treated as a new topic.

### Graph model

`Topic`, unique key `slug`: `slug`, `name`, `topic_type`, `summary`, `derived: true`, `generated_by`, `model`, `generated_at`, `display_name`.

`Event`, unique key `(topic_slug, slug)`: `topic_slug`, `slug`, `name`, `event_type`, `occurred_at`, `summary`, `derived: true`, `generated_by`, `model`, `generated_at`, `display_name`.

```cypher
(:Issue)-[:ABOUT_TOPIC]->(:Topic)
(:Topic)-[:DERIVED_FROM]->(:SourceNode)
(:Event)-[:EVENT_OF_TOPIC]->(:Topic)
(:Event)-[:EVIDENCED_BY]->(:SourceNode)
(:Event)-[:CAUSED {explanation, evidence}]->(:Event)
(:Person)-[:ACTED_IN_EVENT]->(:Event)
```

`DERIVED_FROM` goes to every node that was in the bundle, whether or not it ended up cited as evidence. `EVIDENCED_BY` goes only to the specific nodes cited for that event. `CAUSED` carries `explanation` plus the surviving evidence identifiers for that link, so a causal claim can be checked the same way an event can. Every relationship here carries `derived: true` and `generated_by: "topic-event-extraction-v1"`.

Constraints, following the existing pattern:

```cypher
CREATE CONSTRAINT topic_slug IF NOT EXISTS FOR (t:Topic) REQUIRE t.slug IS UNIQUE
CREATE CONSTRAINT event_key IF NOT EXISTS FOR (e:Event) REQUIRE (e.topic_slug, e.slug) IS UNIQUE
```

### Re-runnability

At the start of every run, every relationship where `generated_by = "topic-event-extraction-v1"` is deleted by that property, then every node with that property is detached and deleted, then the layer is rebuilt from the current bundles. Deletion is by `generated_by` only, never by label or relationship type, so a Task 05 reference edge (identified by `extracted_by`, not `generated_by`) is never touched, and neither is anything from an import.

### `Knowledge` filter group

`backend/app.py` adds a `Knowledge` entry to its source-filter map: `ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED`, `ACTED_IN_EVENT`. `References` and the six source groups are unchanged.

### `PipelineState`

A third timestamp, `last_layer_build_at`, is written to the same `(:PipelineState {id: "singleton"})` node the reference layer already uses. The UI shows a needs-re-run state when `last_extraction_at` is newer than `last_layer_build_at`, or when no build has run yet.

## Relationship Types by Frontend Filter

The backend graph API groups source filters by relationship type in `backend/app.py`.

| Frontend filter | Relationship types included |
| --- | --- |
| `All` | All nodes and all relationships in Neo4j |
| `Mail` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| `Slack` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| `Teams` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| `Issues` | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE`, `HAS_ISSUE_VERSION`, `NEXT_ISSUE_VERSION`, `CHANGED_ISSUE_VERSION`, `HAS_ISSUE_COMMENT`, `WROTE_ISSUE_COMMENT`, `REPLY_TO_ISSUE_COMMENT` |
| `Docs` | `AUTHORED_DOCUMENT`, `HAS_DOCUMENT_VERSION`, `NEXT_DOCUMENT_VERSION`, `AUTHORED_DOCUMENT_VERSION` |
| `PRs` | `AUTHORED_PR`, `REVIEWED_PR`, `HAS_PR_REVIEW`, `WROTE_PR_REVIEW`, `REPLY_TO_PR_REVIEW`, `HAS_CODE_CHANGE` |
| `References` | `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST`, `MENTIONS_DOCUMENT` |
| `Knowledge` | `ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED`, `ACTED_IN_EVENT` |

When a specific source filter is used, the backend returns only nodes connected by those relationship types. This means shared `Person` nodes can appear in multiple source filters.

## Backend Endpoints

`backend/app.py` exposes these:

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/graph?source=<filter>` | `GET` | The graph for one source filter, or `All`. |
| `/api/neo4j/status` | `GET` | Whether the backend can reach Neo4j: `{"connected": true}`. |
| `/api/viewer/start` | `POST` | Starts the SQL viewer as a child process. |
| `/api/viewer/stop` | `POST` | Stops that child process. |
| `/api/references` | `GET` | Current Task 05 reference edges plus pipeline timestamps. |
| `/api/references/extract` | `POST` | Runs the Task 05 deterministic reference extraction pass. |
| `/api/knowledge` | `GET` | Current Task 06 `Topic`/`Event` knowledge layer plus pipeline timestamps. |
| `/api/knowledge/build` | `POST` | Runs the Task 06 LLM-driven extraction pass. `503` with an explanatory message when `OPENAI_API_KEY` is not configured. |
| `/api/ai/chat` | `POST` | Optional chat agent. Takes `{"message": "..."}`, returns `{"answer": "..."}`. |

`/api/ai/chat` is an optional feature and its dependencies are not required by the rest of the backend. The agent is imported on demand inside the endpoint, never at module level, so a missing `langgraph` or `openai` package cannot stop the graph API from starting. When the dependency is absent the endpoint answers `503` with an explanatory message; an empty message answers `400`. The agent also needs `OPENAI_API_KEY` in `.env` at runtime.

Anything that adds another optional feature to the backend should follow the same rule: import it inside the endpoint, and degrade to a clear error instead of taking the graph API down with it.

## Frontend Graph API Shape

The frontend calls:

```text
GET /api/graph?source=All
GET /api/graph?source=Mail
GET /api/graph?source=Slack
GET /api/graph?source=Teams
GET /api/graph?source=Issues
GET /api/graph?source=Docs
GET /api/graph?source=PRs
```

The backend returns:

```json
{
  "source": "All",
  "nodes": [
    {
      "id": "neo4j-element-id",
      "label": "display label",
      "type": "NodeLabel",
      "summary": "short summary",
      "properties": {}
    }
  ],
  "relationships": [
    {
      "id": "neo4j-relationship-element-id",
      "source": "source-node-element-id",
      "target": "target-node-element-id",
      "label": "RELATIONSHIP_TYPE",
      "sourceType": "Mail",
      "properties": {}
    }
  ]
}
```

Label selection in the API prefers these node properties:

1. `display_name`
2. `name`
3. `title`
4. `subject`
5. `person_key`
6. first Neo4j label

Summary selection prefers:

1. `title`
2. `subject`
3. `email`
4. `channel_name`
5. `description`

## What the Graph Does Not Model Yet

These are source facts preserved in SQL or raw JSON properties, but not currently modeled as first-class graph relationships:

- Mail reply chains are stored as `MailMessage.in_reply_to_id`, not `(:MailMessage)-[:REPLY_TO]->(:MailMessage)`.
- No chunking is modeled yet. Text bodies are not split into `Chunk` nodes.
- No embeddings, vector index, or fulltext index are created yet.
- Deterministic identifier references exist as `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST` and `MENTIONS_DOCUMENT`. A reference is only the observation that a text names an entity; it says nothing about why.
- `Topic` and `Event` nodes now exist (Task 06, `backend/topic_event_extraction.py`), with `CAUSED` links between events. They cover one storyline scoped to `Issue` topic candidates. They do not cover PRs or documents as topic candidates in their own right, and there is still no general "this implements that" relationship outside the causal story of an issue.
- Code changes are `CodeChange` nodes, but files and commits are not separate nodes.
- Transcript segment order is stored as `sequence_number`, not represented as `NEXT_SEGMENT` relationships.

## Practical Guidance for Data Generation

To make the graph useful:

- Reuse `source_id`, email addresses, and names consistently so `Person` nodes merge where expected.
- Give at least one Slack message or Teams participant entry per person both an email and a `source_id`. Those bridge rows are what connect the email-carrying sources to the source-ID-only sources.
- Avoid giving two different people the same name when neither has an email or a `source_id`, because a name alone can only attach a nameless observation to one existing identity, never join two.
- Use functional mailbox local parts such as `support`, `noreply`, or `notifications` deliberately. The graph keeps them as `Person` nodes with `actor_type = "mailbox"` so people-focused queries can filter them out.
- Include issue keys, PR numbers, document titles, and meeting references in source text to support later relationship extraction.
- Keep timestamps coherent across SQL sources so event order can be reconstructed.
- Ensure JSONB arrays in SQL contain structured data because importers preserve them as raw JSON strings on graph nodes.
- Remember that raw JSON properties are retained on parent nodes even when the same records are also promoted to first-class retrieval-unit nodes.
