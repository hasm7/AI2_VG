# Graph Database Handoff Specification

This document describes the current Neo4j graph model, how it is derived from PostgreSQL, and how the backend/frontend use it.

The information below was checked against the local Neo4j database read-only. The previous README is not treated as authoritative.

## Current Neo4j Snapshot

Current node counts:

| Label | Count |
| --- | ---: |
| `CodeChange` | 7 |
| `Community` | 2 |
| `Component` | 3 |
| `Document` | 2 |
| `DocumentVersion` | 3 |
| `Event` | 10 |
| `Expertise` | 14 |
| `File` | 4 |
| `Issue` | 2 |
| `IssueComment` | 5 |
| `IssueVersion` | 7 |
| `MailMessage` | 4 |
| `Module` | 2 |
| `Person` | 7 |
| `PipelineState` | 1 |
| `PullRequest` | 2 |
| `PullRequestReview` | 6 |
| `Repository` | 1 |
| `RootCause` | 3 |
| `SlackMessage` | 12 |
| `TeamsMeeting` | 2 |
| `TeamsTranscriptSegment` | 9 |
| `Topic` | 1 |

Current relationship counts:

| Type | Count |
| --- | ---: |
| `ABOUT_TOPIC` | 2 |
| `ACTED_IN_EVENT` | 19 |
| `AFFECTED_COMPONENT` | 10 |
| `AUTHORED_DOCUMENT` | 2 |
| `AUTHORED_DOCUMENT_VERSION` | 3 |
| `AUTHORED_PR` | 2 |
| `CAUSED` | 6 |
| `CHANGED_ISSUE_VERSION` | 7 |
| `COMMENTED_ON_ISSUE` | 4 |
| `COMPONENT_EVIDENCED_BY` | 12 |
| `CONTAINS_FILE` | 4 |
| `CONTAINS_MODULE` | 2 |
| `CONTRIBUTED_TO` | 5 |
| `CREATED_ISSUE` | 2 |
| `DEPENDS_ON` | 2 |
| `DERIVED_FROM` | 51 |
| `EVENT_OF_TOPIC` | 10 |
| `EVIDENCED_BY` | 29 |
| `EXPERTISE_EVIDENCED_BY` | 93 |
| `EXPERTISE_IN` | 14 |
| `HAS_CODE_CHANGE` | 7 |
| `HAS_DOCUMENT_VERSION` | 3 |
| `HAS_EXPERTISE` | 14 |
| `HAS_ISSUE_COMMENT` | 5 |
| `HAS_ISSUE_VERSION` | 7 |
| `HAS_PR_REVIEW` | 6 |
| `HAS_ROOT_CAUSE` | 6 |
| `HAS_TEAMS_TRANSCRIPT_SEGMENT` | 9 |
| `IMPLEMENTED_IN` | 3 |
| `MAIL_RECIPIENT` | 8 |
| `MEMBER_OF_COMMUNITY` | 5 |
| `MENTIONS_DOCUMENT` | 12 |
| `MENTIONS_ISSUE` | 22 |
| `MENTIONS_PULL_REQUEST` | 21 |
| `MODIFIES_FILE` | 7 |
| `NEXT_DOCUMENT_VERSION` | 1 |
| `NEXT_ISSUE_VERSION` | 5 |
| `OWNS_ISSUE` | 2 |
| `PARTICIPATED_IN_MEETING` | 8 |
| `PART_OF_REPOSITORY` | 3 |
| `REPLY_TO_ISSUE_COMMENT` | 1 |
| `REPLY_TO_PR_REVIEW` | 1 |
| `REVIEWED_PR` | 4 |
| `ROOT_CAUSE_EVIDENCED_BY` | 15 |
| `ROOT_CAUSE_IN_COMPONENT` | 5 |
| `SENT_MAIL` | 4 |
| `SENT_SLACK_MESSAGE` | 12 |
| `SLACK_THREAD_REPLY_TO` | 2 |
| `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` | 9 |
| `WORKS_WITH` | 7 |
| `WROTE_ISSUE_COMMENT` | 5 |
| `WROTE_PR_REVIEW` | 6 |

(`Event`, `EVENT_OF_TOPIC`, `CAUSED`, and the counts of everything downstream of the Knowledge layer vary slightly between runs, because the LLM does not propose exactly the same events every time. This snapshot is from the full pipeline rebuild performed for the staleness follow-up work order.)

## Source of Truth and Build Order

PostgreSQL is the source-preserving layer. Neo4j is the derived relationship/memory layer.

The intended build order is:

1. Import SQL source groups from the SQL viewer (`viewer/app.py`).
2. Run deterministic reference extraction (`backend/reference_extraction.py`) to create `MENTIONS_*` edges.
3. Run interpreted knowledge extraction (`backend/topic_event_extraction.py`) to create `Topic`, `Event`, and causal/event evidence relationships.
4. Run the Architecture layer (`backend/architecture_layer.py`) to create `Repository`/`Module`/`File`/`Component` structure.
5. Run the Causal layer (`backend/causal_layer.py`) to create `RootCause` and cross-topic/code/component causal links.
6. Run the Collaboration layer (`backend/collaboration_layer.py`) to create `Expertise` and `WORKS_WITH`.
7. Run the Graph algorithm layer (`backend/graph_algorithms.py`) to create `Community` and precomputed metrics.
8. Run embeddings (`backend/embedding_pass.py`) to add vector properties and vector/fulltext indexes.

Steps 4-7 are each gated on their prerequisites (see each layer's own handoff document) and must be run in that order; the backend returns `409` if a layer's prerequisites are missing.

`PipelineState {id: "singleton"}` stores timestamps for these stages:

| Property | Meaning |
| --- | --- |
| `last_import_at` | A source import last wrote copied source data. |
| `last_extraction_at` | Reference extraction last rebuilt `MENTIONS_*`. |
| `last_layer_build_at` | Knowledge layer last rebuilt `Topic`/`Event`. |
| `last_architecture_build_at` | Architecture layer last rebuilt `Repository`/`Module`/`File`/`Component`. |
| `last_causal_build_at` | Causal layer last rebuilt `RootCause` and causal links. |
| `last_collaboration_build_at` | Collaboration layer last rebuilt `Expertise`/`WORKS_WITH`. |
| `last_algorithms_run_at` | Graph algorithm layer last ran. |
| `last_embedding_at` | Embedding pass last ran. |

`PipelineState` is excluded from `/api/graph` visualization.

Current `PipelineState` snapshot (after a full rebuild of every stage, in order):

| Property | Value |
| --- | --- |
| `last_import_at` | `2026-09-20T13:18:12.864348+00:00` |
| `last_extraction_at` | `2026-09-23T18:08:36.712761+00:00` |
| `last_layer_build_at` | `2026-09-23T18:08:46.846709+00:00` |
| `last_architecture_build_at` | `2026-09-23T18:09:26.465345+00:00` |
| `last_causal_build_at` | `2026-09-23T18:09:39.162792+00:00` |
| `last_collaboration_build_at` | `2026-09-23T18:10:20.780808+00:00` |
| `last_algorithms_run_at` | `2026-09-23T18:10:21.590196+00:00` |
| `last_embedding_at` | `2026-09-23T18:10:21.842346+00:00` |

See `ARCHITECTURE_LAYER_HANDOFF.md`, `CAUSAL_LAYER_HANDOFF.md`, `COLLABORATION_LAYER_HANDOFF.md`, and `GRAPH_ALGORITHMS_HANDOFF.md` for the full detail on each of the four newer layers.

## Pipeline Staleness

Implemented in `backend/pipeline_staleness.py`. This is the single place that computes whether a layer is stale (`needs_rerun` / `needs_layer_rerun`) and why. Every layer's `GET` and `POST` response reads its staleness from here instead of computing it locally.

A stage is stale not only when its *direct* upstream has moved on, but also when that upstream is itself stale — staleness propagates transitively through the whole pipeline. For example, if Reference extraction is stale, Architecture, Causal, Collaboration, Algorithms, and Embeddings are all stale too, even though only Architecture and Embeddings list References as a *direct* upstream.

Three constants define the pipeline:

```python
TIMESTAMP_BY_STAGE = {
    "import": "last_import_at",
    "references": "last_extraction_at",
    "knowledge": "last_layer_build_at",
    "architecture": "last_architecture_build_at",
    "causal": "last_causal_build_at",
    "collaboration": "last_collaboration_build_at",
    "algorithms": "last_algorithms_run_at",
    "embeddings": "last_embedding_at",
}

UPSTREAM_BY_STAGE = {
    "references": ["import"],
    "knowledge": ["references"],
    "architecture": ["import", "references"],
    "causal": ["knowledge", "architecture"],
    "collaboration": ["import", "knowledge", "architecture", "causal"],
    "algorithms": ["knowledge", "architecture", "causal", "collaboration"],
    "embeddings": ["import", "knowledge", "architecture", "causal"],
}

STAGE_LABELS = {
    "import": "Import",
    "references": "Reference extraction",
    "knowledge": "Knowledge layer",
    "architecture": "Architecture layer",
    "causal": "Root cause & impact layer",
    "collaboration": "Expertise & collaboration layer",
    "algorithms": "Graph algorithms",
    "embeddings": "Embeddings",
}
```

`import` is a source, not a layer; it never has a `stale` status of its own, only a timestamp other stages compare against.

`compute_staleness(pipeline_state)` returns `{stage: {"stale": bool, "reasons": [str]}}` for every stage except `"import"`, evaluated in `UPSTREAM_BY_STAGE` order (which is already dependency order, so every stage's upstream stages are always resolved before it):

1. If a stage's own timestamp is missing, it is stale with the single reason `"Never built."`, and no further rules are checked for it.
2. Otherwise, for each of its upstream stages `U`, in the order listed in `UPSTREAM_BY_STAGE`:
   - if `U`'s timestamp exists and is newer than the stage's own timestamp, add the reason `"<label of U> was rebuilt after this layer."`;
   - if `U` is not `import` and `U` is itself stale, add the reason `"<label of U> is stale."`.
3. The stage is stale if it collected at least one reason.

A missing upstream timestamp is always covered by rule 1 turning that upstream stale, which in turn is picked up by the second bullet of rule 2 as `"<label> is stale."` — there is no separate case for "upstream missing but not stale."

Every layer's state payload includes both the boolean field (named `needs_rerun`, except the Knowledge layer's `needs_layer_rerun`, for backward compatibility) and a `stale_reasons: string[]` field with the human-readable reasons, in the order `compute_staleness` produced them. The frontend renders each reason on its own line under the stale warning.

`backend/pipeline_staleness.py` also owns the one `read_pipeline_state(tx)` query that reads all eight timestamps at once; every layer module's own `read_pipeline_state` now delegates to it, so prerequisite checks (the `409` responses) and staleness computation always see a consistent snapshot of `PipelineState`.

## SQL to Graph Model

| Source | SQL tables | Graph labels | Relationships |
| --- | --- | --- | --- |
| Mail | `mail_messages` | `MailMessage`, `Person` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| Slack | `slack_messages` | `SlackMessage`, `Person` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| Teams | `teams_meetings`, `teams_transcript_segments` | `TeamsMeeting`, `TeamsTranscriptSegment`, `Person` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| Issues | `issues`, `issue_versions`, `issue_comments` | `Issue`, `IssueVersion`, `IssueComment`, `Person` | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE`, `HAS_ISSUE_VERSION`, `NEXT_ISSUE_VERSION`, `CHANGED_ISSUE_VERSION`, `HAS_ISSUE_COMMENT`, `WROTE_ISSUE_COMMENT`, `REPLY_TO_ISSUE_COMMENT` |
| Docs | `document_versions` | `Document`, `DocumentVersion`, `Person` | `AUTHORED_DOCUMENT`, `HAS_DOCUMENT_VERSION`, `NEXT_DOCUMENT_VERSION`, `AUTHORED_DOCUMENT_VERSION` |
| PRs | `pr_versions`, `pr_reviews`, `pr_versions.code_changes` | `PullRequest`, `PullRequestReview`, `CodeChange`, `Person` | `AUTHORED_PR`, `REVIEWED_PR`, `HAS_PR_REVIEW`, `WROTE_PR_REVIEW`, `REPLY_TO_PR_REVIEW`, `HAS_CODE_CHANGE` |
| References | Graph text scan | No new nodes | `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST`, `MENTIONS_DOCUMENT` |
| Knowledge | Graph bundles around issues | `Topic`, `Event` | `ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED`, `ACTED_IN_EVENT` |
| Architecture | `CodeChange.file_path`, PR/review/document/message bundles | `Repository`, `Module`, `File`, `Component` | `CONTAINS_MODULE`, `CONTAINS_FILE`, `MODIFIES_FILE`, `IMPLEMENTED_IN`, `PART_OF_REPOSITORY`, `DEPENDS_ON`, `COMPONENT_EVIDENCED_BY` |
| Causal | Topic-centered bundles around events | `RootCause` | `CROSS_TOPIC_CAUSED`, `HAS_ROOT_CAUSE`, `ROOT_CAUSE_IN_COMPONENT`, `ROOT_CAUSE_EVIDENCED_BY`, `CONTRIBUTED_TO`, `AFFECTED_COMPONENT` |
| Collaboration | Activity relationships already in the graph | `Expertise` | `HAS_EXPERTISE`, `EXPERTISE_IN`, `EXPERTISE_EVIDENCED_BY`, `WORKS_WITH` |
| Algorithms | `WORKS_WITH` graph, `Expertise`, `DEPENDS_ON`, `AFFECTED_COMPONENT` | `Community` | `MEMBER_OF_COMMUNITY` |

## Constraints and Indexes

Uniqueness constraints:

| Constraint | Label | Key |
| --- | --- | --- |
| `person_key` | `Person` | `person_key` |
| `mail_message_key` | `MailMessage` | `source_instance`, `message_id` |
| `slack_message_key` | `SlackMessage` | `source_instance`, `workspace_id`, `channel_id`, `message_id`, `version_number` |
| `teams_meeting_key` | `TeamsMeeting` | `source_instance`, `meeting_id` |
| `transcript_segment_key` | `TeamsTranscriptSegment` | `source_instance`, `meeting_id`, `segment_id` |
| `issue_node_key` | `Issue` | `source_instance`, `issue_id` |
| `issue_version_key` | `IssueVersion` | `source_instance`, `issue_id`, `version_number` |
| `issue_comment_key` | `IssueComment` | `source_instance`, `comment_id` |
| `document_node_key` | `Document` | `source_instance`, `document_id` |
| `document_version_key` | `DocumentVersion` | `source_instance`, `document_id`, `version_number` |
| `pull_request_key` | `PullRequest` | `source_instance`, `repository`, `pr_number` |
| `pull_request_review_key` | `PullRequestReview` | `source_instance`, `repository`, `pr_number`, `source_id` |
| `code_change_key` | `CodeChange` | `source_instance`, `repository`, `pr_number`, `version_number`, `file_path` |
| `topic_slug` | `Topic` | `slug` |
| `event_key` | `Event` | `topic_slug`, `slug` |
| `repository_key` | `Repository` | `source_instance`, `name` |
| `module_key` | `Module` | `source_instance`, `repository`, `path` |
| `file_key` | `File` | `source_instance`, `repository`, `path` |
| `component_key` | `Component` | `source_instance`, `repository`, `slug` |
| `root_cause_slug` | `RootCause` | `slug` |
| `expertise_key` | `Expertise` | `person_key`, `subject_label`, `subject_key` |
| `community_key` | `Community` | `community_id` |

Vector indexes currently online:

- `mailmessage_embedding`
- `slackmessage_embedding`
- `teamstranscriptsegment_embedding`
- `issueversion_embedding`
- `issuecomment_embedding`
- `documentversion_embedding`
- `pullrequest_embedding`
- `pullrequestreview_embedding`
- `topic_embedding`
- `event_embedding`
- `component_embedding`
- `rootcause_embedding`

Fulltext indexes currently online:

- `entity_lookup` on `Person`, `Issue`, `Document`, `PullRequest`, `Topic` properties `name`, `display_name`, `title`
- `issue_key_lookup` on `Issue.issue_key`

## Shared `Person` Model

`Person` nodes are resolved globally before source relationships are written. Import code never creates one-off persons per table row; it writes canonical person clusters and then relationships match by `person_key`.

Resolution happens in `viewer/person_identity.py`:

1. Collect observations from every person-bearing SQL field.
2. Merge by normalized email.
3. Merge by source ID. `SOURCE_ID_IS_GLOBAL = True`, so source IDs are treated as globally unique.
4. Use name-only observations only when exactly one established identity carries that normalized name.
5. Mark ambiguous name-only observations with `identity_ambiguous = true`.

Current `Person` nodes:

| Person key | Name | Emails | Source IDs | Actor type | Confidence |
| --- | --- | --- | --- | --- | --- |
| `email:anna.berg@example.com` | Anna Berg | `anna.berg@example.com` | `u-annab` | person | strong |
| `email:anna.lindqvist@example.com` | Anna Lindqvist | `anna.lindqvist@example.com` | `u-anna` | person | strong |
| `email:erik.nilsson@example.com` | Erik Nilsson | `erik.nilsson@example.com` | `u-erik` | person | strong |
| `email:martin.ek@northwind.example.com` | Martin Ek | `martin.ek@northwind.example.com` | | person | strong |
| `email:support@example.com` | Support | `support@example.com` | | mailbox | strong |
| `source:u-priya` | Priya Raman | | `u-priya` | person | strong |
| `name:anna` | Anna | | | person | weak, ambiguous |

## Source Object Nodes

### `MailMessage`

Key: `(source_instance, message_id)`.

Copied properties include `source_instance`, `message_id`, `name`, `display_name`, `sender_address`, `sender_name`, `recipients_raw`, `subject`, `body`, `sent_at`, `in_reply_to_id`, `source_url`.

Relationships:

```cypher
(:Person)-[:SENT_MAIL]->(:MailMessage)
(:MailMessage)-[:MAIL_RECIPIENT {recipient_type}]->(:Person)
```

Current data: 4 mail nodes, 4 `SENT_MAIL`, 8 `MAIL_RECIPIENT`. Reply IDs are stored as `in_reply_to_id`; no explicit mail-reply relationship exists.

### `SlackMessage`

Key: `(source_instance, workspace_id, channel_id, message_id, version_number)`.

Copied properties include `source_instance`, `workspace_id`, `channel_id`, `message_id`, `version_number`, `channel_name`, `author_source_id`, `author_name`, `author_email`, `body`, `sent_at`, `version_at`, `thread_root_id`, `source_url`, `name`, `display_name`.

Relationships:

```cypher
(:Person)-[:SENT_SLACK_MESSAGE]->(:SlackMessage)
(:SlackMessage)-[:SLACK_THREAD_REPLY_TO]->(:SlackMessage)
```

Current data: 12 Slack version nodes, including `slack-006` versions 1 and 2. Two thread replies exist.

### `TeamsMeeting`

Key: `(source_instance, meeting_id)`.

Copied properties include `source_instance`, `meeting_id`, `title`, `started_at`, `ended_at`, `participants_raw`, `source_url`, `name`, `display_name`.

Relationships:

```cypher
(:Person)-[:PARTICIPATED_IN_MEETING]->(:TeamsMeeting)
(:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(:TeamsTranscriptSegment)
```

Current data: 2 meeting nodes, 8 participant edges.

### `TeamsTranscriptSegment`

Key: `(source_instance, meeting_id, segment_id)`.

Copied properties include `source_instance`, `meeting_id`, `segment_id`, `sequence_number`, `speaker_source_id`, `speaker_name`, `start_offset_ms`, `end_offset_ms`, `body`, `name`, `display_name`.

Relationships:

```cypher
(:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(:TeamsTranscriptSegment)
(:Person)-[:SPOKE_TEAMS_TRANSCRIPT_SEGMENT]->(:TeamsTranscriptSegment)
```

Current data: 9 segment nodes and 9 speaker edges. Segment order is stored in `sequence_number`; no `NEXT_SEGMENT` relationship exists.

### `Issue`

Key: `(source_instance, issue_id)`.

Parent `Issue` nodes combine stable identity from `issues` with latest state from the highest `issue_versions.version_number`.

Properties include `source_instance`, `issue_id`, `issue_key`, `issue_type`, `title`, `description`, `acceptance_criteria`, `status`, `priority`, `creator_name`, `creator_source_id`, `assignee_name`, `assignee_source_id`, `created_at`, latest `version_at`, latest `version_number`, `version_count`, `versions_raw`, `comment_count`, `comments_raw`, `source_url`, `name`, `display_name`.

Relationships:

```cypher
(:Person)-[:CREATED_ISSUE]->(:Issue)
(:Person)-[:OWNS_ISSUE]->(:Issue)
(:Person)-[:COMMENTED_ON_ISSUE]->(:Issue)
(:Issue)-[:HAS_ISSUE_VERSION]->(:IssueVersion)
(:Issue)-[:HAS_ISSUE_COMMENT]->(:IssueComment)
```

Current data: `AUTH-17` and `AUTH-19`.

### `IssueVersion`

Key: `(source_instance, issue_id, version_number)`.

One node per SQL `issue_versions` row. Properties include all version fields plus `name`/`display_name` as `<issue_key> v<version_number>`.

Relationships:

```cypher
(:Issue)-[:HAS_ISSUE_VERSION]->(:IssueVersion)
(:IssueVersion)-[:NEXT_ISSUE_VERSION]->(:IssueVersion)
(:Person)-[:CHANGED_ISSUE_VERSION]->(:IssueVersion)
```

Current data: 7 version nodes and 5 `NEXT_ISSUE_VERSION` links.

### `IssueComment`

Key: `(source_instance, comment_id)`.

One node per latest comment row; `version_count` records how many SQL versions that comment has.

Relationships:

```cypher
(:Issue)-[:HAS_ISSUE_COMMENT]->(:IssueComment)
(:Person)-[:WROTE_ISSUE_COMMENT]->(:IssueComment)
(:IssueComment)-[:REPLY_TO_ISSUE_COMMENT]->(:IssueComment)
```

Current data: 5 comment nodes and one reply (`comment-003` replies to `comment-002`).

### `Document`

Key: `(source_instance, document_id)`.

Parent `Document` nodes hold latest document state. Earlier versions are retained in `versions_raw`.

Properties include `source_instance`, `document_id`, `document_type`, `title`, `body`, `content_format`, `author_name`, `author_source_id`, `created_at`, `version_at`, `version_number`, `change_summary`, `versions_raw`, `source_url`, `name`, `display_name`.

Relationships:

```cypher
(:Person)-[:AUTHORED_DOCUMENT]->(:Document)
(:Document)-[:HAS_DOCUMENT_VERSION]->(:DocumentVersion)
```

Current data: `doc-001` (`REQ-AUTH-SESSION`) and `doc-002` (`Session refresh path`).

### `DocumentVersion`

Key: `(source_instance, document_id, version_number)`.

One node per SQL `document_versions` row.

Relationships:

```cypher
(:Document)-[:HAS_DOCUMENT_VERSION]->(:DocumentVersion)
(:DocumentVersion)-[:NEXT_DOCUMENT_VERSION]->(:DocumentVersion)
(:Person)-[:AUTHORED_DOCUMENT_VERSION]->(:DocumentVersion)
```

Current data: 3 version nodes and one version-chain edge for `doc-001`.

### `PullRequest`

Key: `(source_instance, repository, pr_number)`.

Parent `PullRequest` nodes hold latest PR state. Reviews and code changes are also retained in raw JSON properties.

Properties include `source_instance`, `repository`, `pr_number`, `title`, `description`, `author_name`, `author_source_id`, `state`, `created_at`, `version_at`, `version_number`, `base_commit`, `head_commit`, `code_changes_raw`, `reviews_raw`, `source_url`, `name`, `display_name`.

Relationships:

```cypher
(:Person)-[:AUTHORED_PR]->(:PullRequest)
(:Person)-[:REVIEWED_PR]->(:PullRequest)
(:PullRequest)-[:HAS_PR_REVIEW]->(:PullRequestReview)
(:PullRequest)-[:HAS_CODE_CHANGE]->(:CodeChange)
```

Current data: `backend-api#42` and `backend-api#47`.

### `PullRequestReview`

Key: `(source_instance, repository, pr_number, source_id)`.

One node per latest review/comment row. `version_count` records how many SQL versions that review entry has.

Relationships:

```cypher
(:PullRequest)-[:HAS_PR_REVIEW]->(:PullRequestReview)
(:Person)-[:WROTE_PR_REVIEW]->(:PullRequestReview)
(:PullRequestReview)-[:REPLY_TO_PR_REVIEW]->(:PullRequestReview)
```

Current data: 6 review nodes and one reply (`review-003` replies to `review-002`).

### `CodeChange`

Key: `(source_instance, repository, pr_number, version_number, file_path)`.

One node is created for each object in each `pr_versions.code_changes` JSONB array. This means a file can appear more than once across PR versions because `version_number` is part of the key.

Properties include parent PR key fields plus `file_path`, `change_type`, `before_summary`, `after_summary`, `diff`, `name`, `display_name`.

Relationship:

```cypher
(:PullRequest)-[:HAS_CODE_CHANGE]->(:CodeChange)
```

Current data: 7 code-change nodes.

## Deterministic Reference Layer

Implemented in `backend/reference_extraction.py`.

This layer scans graph text for explicit identifiers and creates only relationships:

```cypher
(:SourceNode)-[:MENTIONS_ISSUE]->(:Issue)
(:SourceNode)-[:MENTIONS_PULL_REQUEST]->(:PullRequest)
(:SourceNode)-[:MENTIONS_DOCUMENT]->(:Document)
```

Relationship properties:

| Property | Meaning |
| --- | --- |
| `derived` | `true` |
| `extracted_by` | `reference-extraction-v1` |
| `matched_text` | Exact matched text. |
| `source_property` | Property where the text was found. |
| `extracted_at` | Extraction timestamp. |

Scanned labels/properties:

| Label | Properties |
| --- | --- |
| `MailMessage` | `subject`, `body` |
| `SlackMessage` | `body` |
| `TeamsMeeting` | `title` |
| `TeamsTranscriptSegment` | `body` |
| `Issue` | `title`, `description` |
| `IssueVersion` | `title`, `description`, `acceptance_criteria` |
| `IssueComment` | `body` |
| `Document` | `title`, `body` |
| `DocumentVersion` | `title`, `body`, `change_summary` |
| `PullRequest` | `title`, `description` |
| `PullRequestReview` | `body` |
| `CodeChange` | `before_summary`, `after_summary` |

Current reference-edge counts:

- `MENTIONS_ISSUE`: 22
- `MENTIONS_PULL_REQUEST`: 21
- `MENTIONS_DOCUMENT`: 12

The extraction pass deletes only relationships with `extracted_by = "reference-extraction-v1"` before rebuilding.

## Interpreted Knowledge Layer

Implemented in `backend/topic_event_extraction.py`.

This layer builds issue-centered bundles and uses the OpenAI Responses API with structured output to create interpreted `Topic` and `Event` nodes.

Current graph has:

- 1 `Topic`
- 10 `Event`
- 2 `ABOUT_TOPIC`
- 51 `DERIVED_FROM`
- 10 `EVENT_OF_TOPIC`
- 29 `EVIDENCED_BY`
- 19 `ACTED_IN_EVENT`
- 6 `CAUSED`

(These counts vary slightly run to run; see the note under the node/relationship count tables above.)

Model constants in code:

| Constant | Value |
| --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra` |
| `OPENAI_REASONING_EFFORT` | `medium` |
| `EXTRACTION_VERSION` | `topic-event-extraction-v1` |

Graph model:

```cypher
(:Issue)-[:ABOUT_TOPIC]->(:Topic)
(:Topic)-[:DERIVED_FROM]->(:SourceNode)
(:Event)-[:EVENT_OF_TOPIC]->(:Topic)
(:Event)-[:EVIDENCED_BY]->(:SourceNode)
(:Person)-[:ACTED_IN_EVENT]->(:Event)
(:Event)-[:CAUSED {explanation, evidence}]->(:Event)
```

`Topic` properties: `slug`, `name`, `topic_type`, `summary`, `derived`, `generated_by`, `model`, `generated_at`, `display_name`.

`Event` properties: `topic_slug`, `slug`, `name`, `event_type`, `occurred_at`, `summary`, `derived`, `generated_by`, `model`, `generated_at`, `display_name`.

The layer deletes only nodes/relationships with `generated_by = "topic-event-extraction-v1"` before rebuilding.

## Embedding Layer

Implemented in `backend/embedding_pass.py`.

Embeddings are present in the current graph. The previous "no embeddings yet" assumption is outdated.

Configuration:

| Setting | Value |
| --- | --- |
| Version | `embedding-v1` |
| Model | `text-embedding-3-large` |
| Dimensions | 1536 |
| Similarity | cosine |
| Batch size | 100 |

Embedded labels:

- `MailMessage`
- `SlackMessage`
- `TeamsTranscriptSegment`
- `IssueVersion`
- `IssueComment`
- `DocumentVersion`
- `PullRequestReview`
- `PullRequest`
- `Topic`
- `Event`
- `Component`
- `RootCause`

Each embedded node gets:

- `embedding`
- `embedding_model`
- `embedding_source_hash`
- `embedded_at`

The pass is incremental: if the assembled text hash and model match, the node is skipped unless `force=True`.

## Frontend Filter Mapping

`backend/app.py` maps frontend filters to relationship types:

| Filter | Relationship types |
| --- | --- |
| `All` | All relationships and all non-`PipelineState` nodes. |
| `Mail` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| `Slack` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| `Teams` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| `Issues` | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE`, `HAS_ISSUE_VERSION`, `NEXT_ISSUE_VERSION`, `CHANGED_ISSUE_VERSION`, `HAS_ISSUE_COMMENT`, `WROTE_ISSUE_COMMENT`, `REPLY_TO_ISSUE_COMMENT` |
| `Docs` | `AUTHORED_DOCUMENT`, `HAS_DOCUMENT_VERSION`, `NEXT_DOCUMENT_VERSION`, `AUTHORED_DOCUMENT_VERSION` |
| `PRs` | `AUTHORED_PR`, `REVIEWED_PR`, `HAS_PR_REVIEW`, `WROTE_PR_REVIEW`, `REPLY_TO_PR_REVIEW`, `HAS_CODE_CHANGE` |
| `References` | `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST`, `MENTIONS_DOCUMENT` |
| `Knowledge` | `ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED`, `ACTED_IN_EVENT` |
| `Architecture` | `CONTAINS_MODULE`, `CONTAINS_FILE`, `MODIFIES_FILE`, `IMPLEMENTED_IN`, `PART_OF_REPOSITORY`, `DEPENDS_ON`, `COMPONENT_EVIDENCED_BY` |
| `Causal` | `CAUSED`, `CROSS_TOPIC_CAUSED`, `HAS_ROOT_CAUSE`, `ROOT_CAUSE_IN_COMPONENT`, `ROOT_CAUSE_EVIDENCED_BY`, `CONTRIBUTED_TO`, `AFFECTED_COMPONENT` |
| `Collaboration` | `HAS_EXPERTISE`, `EXPERTISE_IN`, `EXPERTISE_EVIDENCED_BY`, `WORKS_WITH` |
| `Algorithms` | `MEMBER_OF_COMMUNITY` |

When a specific filter is selected, `/api/graph` returns nodes connected by those relationship types. Shared `Person` nodes can therefore appear in multiple filters.

## Backend API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/graph?source=<filter>` | `GET` | Graph nodes and relationships for a filter or `All`. |
| `/api/neo4j/status` | `GET` | Neo4j connectivity check. |
| `/api/viewer/start` | `POST` | Starts the SQL viewer child process. |
| `/api/viewer/stop` | `POST` | Stops the SQL viewer child process. |
| `/api/references` | `GET` | Current reference extraction state and edges. |
| `/api/references/extract` | `POST` | Rebuilds deterministic reference edges. |
| `/api/knowledge` | `GET` | Current `Topic`/`Event` knowledge layer. |
| `/api/knowledge/build` | `POST` | Rebuilds LLM-driven knowledge layer. |
| `/api/architecture` | `GET` | Current `Repository`/`Module`/`File`/`Component` architecture layer. |
| `/api/architecture/build` | `POST` | Rebuilds the architecture layer (deterministic structure plus LLM components). |
| `/api/causal` | `GET` | Current `RootCause`/causal-link layer. |
| `/api/causal/build` | `POST` | Rebuilds the causal layer. |
| `/api/collaboration` | `GET` | Current `Expertise`/`WORKS_WITH` collaboration layer. |
| `/api/collaboration/build` | `POST` | Rebuilds the collaboration layer (deterministic). |
| `/api/algorithms` | `GET` | Current graph algorithm results (`Community`, precomputed metrics). |
| `/api/algorithms/run` | `POST` | Runs the `networkx`-based graph algorithm layer. |
| `/api/embeddings` | `GET` | Embedding state by label. |
| `/api/embeddings/build` | `POST` | Runs incremental or forced embedding pass. |
| `/api/ai/chat` | `POST` | Optional LangGraph/OpenAI chat agent. |

The graph response shape is:

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

## What Is Not Modeled as First-Class Graph Structure

- Mail replies are stored as `MailMessage.in_reply_to_id`, not as a relationship.
- Transcript sequence is stored as `TeamsTranscriptSegment.sequence_number`, not as `NEXT_SEGMENT`.
- Files and commits are not separate nodes; they are properties on `CodeChange`/`PullRequest`.
- SQL raw JSON properties such as `versions_raw`, `comments_raw`, `reviews_raw`, and `code_changes_raw` are kept for source context, but traversal should prefer first-class version/comment/review/code-change nodes.
- `MENTIONS_*` edges mean a text names an entity; they do not imply implementation, causality, ownership, or dependency.
- `Topic`/`Event` is issue-centered. PRs and documents are not independent topic candidates yet.
