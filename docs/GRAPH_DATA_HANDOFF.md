# Graph Database Handoff Specification

This document describes how the PostgreSQL source tables are imported into Neo4j and how the graph is exposed to the React frontend.

The SQL database is the source-preserving layer. Neo4j is the relationship and memory layer. The graph model intentionally keeps the six source groups visible through node labels and relationship types instead of flattening everything into one generic node model.

## Related Documents and Code

- SQL source schema: `docs/SQL_DATA_HANDOFF.md`
- PostgreSQL DDL: `scripts/setup_postgres_schema.py`
- SQL-to-Neo4j import code: `viewer/app.py`
- Person identity resolution: `viewer/person_identity.py`
- Graph API used by frontend: `backend/app.py`
- Frontend graph renderer: `frontend/src/main.tsx`

## Import Flow

Each source group is imported from the SQL viewer through source-specific import actions in `viewer/app.py`.

| Source group | SQL tables | Neo4j labels | Main relationships |
| --- | --- | --- | --- |
| Mail | `mail_messages` | `MailMessage`, `Person` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| Slack / project chat | `slack_messages` | `SlackMessage`, `Person` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` | `TeamsMeeting`, `TeamsTranscriptSegment`, `Person` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| Issues / tickets | `issues`, `issue_versions`, `issue_comments` | `Issue`, `Person` | `OWNS_ISSUE`, `COMMENTED_ON_ISSUE` |
| Requirements and technical documentation | `document_versions` | `Document`, `Person` | `AUTHORED_DOCUMENT` |
| Pull requests, code reviews, and code changes | `pr_versions`, `pr_reviews` | `PullRequest`, `Person` | `AUTHORED_PR`, `REVIEWED_PR` |

The imports are idempotent at the node/relationship level because they use `MERGE` with unique keys.

Every import first runs person identity resolution over **all** source tables, not only the tables of the source group being imported. `Person` nodes are written from the resolved identity clusters before any relationship is created, so all six source groups agree on the same `Person` nodes no matter which import runs.

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

The `emails`, `source_ids` and `names` lists are the lookup surface for later extraction steps that need to match a name mentioned in free text back to a person.

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
| `Document` | `(source_instance, document_id)` |
| `PullRequest` | `(source_instance, repository, pr_number)` |

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
(:Person)-[:OWNS_ISSUE]->(:Issue)
(:Person)-[:COMMENTED_ON_ISSUE]->(:Issue)
```

Notes:

- The graph keeps one `Issue` node per `(source_instance, issue_id)`. Identity comes from `issues`; the node's main state comes from the **latest** version, selected by the highest `version_number`. This matches how `Document` and `PullRequest` are imported.
- Every version is embedded in `Issue.versions_raw`, ordered by `version_number` ascending, with `version_count` holding how many there are. Each entry holds `version_number`, `version_at`, `status`, `issue_type`, `title`, `priority`, `assignee_name`, `assignee_source_id`, `changed_by_name` and `changed_by_id`.
- Issue comments are not separate graph nodes. They are embedded as JSON in `Issue.comments_raw`.
- The owner person is the assignee when present, otherwise the creator.
- One `COMMENTED_ON_ISSUE` relationship is created per distinct comment author.

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
```

Notes:

- The graph keeps one `Document` node per `(source_instance, document_id)`.
- The latest SQL version becomes the main node state.
- Earlier versions are embedded in `versions_raw`.

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
```

Notes:

- The graph keeps one `PullRequest` node per `(source_instance, repository, pr_number)`.
- The latest SQL PR version becomes the main node state.
- PR reviews are not separate graph nodes. They are embedded as JSON in `PullRequest.reviews_raw`.
- One `REVIEWED_PR` relationship is created per distinct review author.

## Relationship Types by Frontend Filter

The backend graph API groups source filters by relationship type in `backend/app.py`.

| Frontend filter | Relationship types included |
| --- | --- |
| `All` | All nodes and all relationships in Neo4j |
| `Mail` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| `Slack` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| `Teams` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| `Issues` | `OWNS_ISSUE`, `COMMENTED_ON_ISSUE` |
| `Docs` | `AUTHORED_DOCUMENT` |
| `PRs` | `AUTHORED_PR`, `REVIEWED_PR` |

When a specific source filter is used, the backend returns only nodes connected by those relationship types. This means shared `Person` nodes can appear in multiple source filters.

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
- Issue version history exists in SQL and is carried onto the node as `Issue.versions_raw`, but it is not traversable as graph structure. The graph cannot yet query status transitions, ask when an issue stalled, or connect a transition to the Slack message or pull request around it. Versions are not `IssueVersion` nodes and there is no `NEXT_VERSION` relationship.
- Issue comments are embedded in `Issue.comments_raw`, not represented as `IssueComment` nodes.
- Document earlier versions are embedded in `Document.versions_raw`, not represented as `DocumentVersion` nodes.
- PR reviews are embedded in `PullRequest.reviews_raw`, not represented as `PullRequestReview` nodes.
- PR code changes are embedded in `PullRequest.code_changes_raw`, not represented as file, commit, or hunk nodes.
- Transcript segment order is stored as `sequence_number`, not represented as `NEXT_SEGMENT` relationships.
- Cross-source semantic links, such as an issue mentioned in Slack or a PR implementing an issue, are not currently explicit Neo4j relationships unless represented indirectly through people or text content.

## Practical Guidance for Data Generation

To make the graph useful:

- Reuse `source_id`, email addresses, and names consistently so `Person` nodes merge where expected.
- Give at least one Slack message or Teams participant entry per person both an email and a `source_id`. Those bridge rows are what connect the email-carrying sources to the source-ID-only sources.
- Avoid giving two different people the same name when neither has an email or a `source_id`, because a name alone can only attach a nameless observation to one existing identity, never join two.
- Include issue keys, PR numbers, document titles, and meeting references in source text to support later relationship extraction.
- Keep timestamps coherent across SQL sources so event order can be reconstructed.
- Ensure JSONB arrays in SQL contain structured data because importers preserve them as raw JSON strings on graph nodes.
- Remember that some rich source records are embedded as raw JSON on parent nodes rather than becoming separate graph nodes.
