# Graph Data Handoff: the Neo4j Memory Graph

This is the reference for the Neo4j graph: every node label, its key and properties, every relationship type, which
code creates it, the constraints and indexes, the bookkeeping node, the graph API and the graph panel.

**Verified on 2026-09-30** against the live database (Neo4j 2026.08.1 Enterprise, read-only session: labels,
relationship types with their start and end labels, every property key and value type per label, constraints,
indexes) and against the code that writes the graph (`viewer/app.py` for the import, one module per layer in
`backend/`).

Related: `docs/SQL_DATA_HANDOFF.md` (the rows this is built from), `docs/PIPELINE_AND_LINKS_HANDOFF.md` (how layers
depend on each other), one `docs/*_LAYER_HANDOFF.md` per layer.

## 1. What the graph is

PostgreSQL holds the source records. Neo4j holds the same records as nodes, plus everything the layers derive from
them: explicit references, topics and events, code structure and components, root causes, expertise, collaboration,
communities, metrics and search vectors. Neo4j can always be rebuilt from PostgreSQL: import, then build the seven
layers in order.

Three kinds of content live in the graph:

| Kind | Written by | Marked by | Deleted by |
| --- | --- | --- | --- |
| **Source nodes and relationships** (copies of SQL rows) | the SQL import (`viewer/app.py`) | no marker; relationships have no properties except `MAIL_RECIPIENT.recipient_type` | never (the import only merges) |
| **Derived nodes and relationships** | layers 1 to 6 and the chunk nodes of layer 7 | `derived = true` and `generated_by = <layer version>` (layer 1 uses `extracted_by`) | the owning layer, by that marker, on rebuild |
| **Derived properties on existing nodes** | layer 6 (metrics) and layer 7 (vectors) | `algorithms_generated_by` / `embedding_version` | overwritten or removed by the owning layer |

## 2. Current snapshot

The Kvitta data (`data/kvitta_seed.sql`), imported and built layer by layer on 2026-09-29, all seven layers. Without
`PipelineState`: **542 nodes and 2 851 relationships**; nothing is stale. The SQL import alone gives 294 nodes and 617
relationships, so the layers almost double the nodes and multiply the relationships by 4.6.

| Label | Count | Created by |
| --- | ---: | --- |
| `MailMessage` | 14 | import |
| `SlackMessage` | 50 | import |
| `TeamsMeeting` | 10 | import |
| `TeamsTranscriptSegment` | 45 | import |
| `Issue` | 12 | import |
| `IssueVersion` | 34 | import |
| `IssueComment` | 17 | import |
| `Document` | 6 | import |
| `DocumentVersion` | 10 | import |
| `PullRequest` | 14 | import |
| `PullRequestReview` | 33 | import |
| `CodeChange` | 39 | import |
| `Person` | 10 | import (identity resolution) |
| `Topic` | 10 | Knowledge layer |
| `Event` | 51 | Knowledge layer |
| `Repository` | 3 | Architecture layer |
| `Module` | 13 | Architecture layer |
| `File` | 23 | Architecture layer |
| `Component` | 18 | Architecture layer |
| `RootCause` | 15 | Root cause & impact layer |
| `Expertise` | 109 | Expertise & collaboration layer |
| `Community` | 2 | Graph algorithms |
| `EmbeddingChunk` | 4 | Embeddings (the two versions of `doc-002`, the only texts over 12 000 characters) |
| `PipelineState` | 1 | every stage (bookkeeping) |
| `Searchable` (extra label) | 364 | Embeddings; on the 360 embedded nodes and the 4 chunks |

Per stage: import 294 nodes and 617 relationships; Reference extraction 293 relationships; Knowledge 61 nodes and 733
relationships; Architecture 57 and 188; Root cause & impact 15 and 186; Expertise & collaboration 109 and 822; Graph
algorithms 2 and 8; Embeddings 4 and 4.

LLM-derived counts (events, root causes, components, expertise and everything built on them) vary a little between
rebuilds, because the model does not propose exactly the same output every time.

## 3. Conventions

- **Keys.** Every source node is keyed by the SQL primary key of its row (without `version_number` where the node
  stands for the whole object). Keys are enforced by uniqueness constraints (section 7).
- **`name` and `display_name`.** Every node that is shown has `display_name`, and source nodes also `name` (the same
  value). The graph API labels a node with `display_name`, else `name`, `title`, `subject`, `person_key`.
- **Timestamps** are ISO 8601 **strings** with offset (`2026-03-03T14:25:00+01:00`), copied from PostgreSQL. The
  layers' own timestamps (`generated_at`, `extracted_at`, `embedded_at`, `PipelineState`) are UTC ISO strings
  (`...+00:00`).
- **Raw JSON properties** (`recipients_raw`, `participants_raw`, `versions_raw`, `comments_raw`, `reviews_raw`,
  `code_changes_raw`) are JSON strings kept for context. No layer scans or traverses them; use the first-class nodes.
- **Relationship direction** is fixed per type (tables below). Queries should match the stated direction.
- **The `Searchable` label** is an extra label on embedded nodes; it is never a node's type (the graph API skips it).

## 4. Source nodes (written by the import)

The import is described row by row in `docs/SQL_DATA_HANDOFF.md`, section 9. Properties below are exactly those found
on the live nodes (embedding properties, section 6, are left out here).

### `MailMessage`

Key `(source_instance, message_id)`. Properties: `source_instance`, `message_id`, `name` (= `message_id`),
`display_name` (= `message_id`), `sender_address`, `sender_name`, `recipients_raw`, `subject`, `body`, `sent_at`,
`in_reply_to_id` (absent when NULL), `source_url`. No reply relationship; `in_reply_to_id` is a property.

### `SlackMessage`

One node per **version**. Key `(source_instance, workspace_id, channel_id, message_id, version_number)`. Properties:
`source_instance`, `workspace_id`, `channel_id`, `message_id`, `version_number`, `channel_name`, `name` and
`display_name` (both = `message_id`, **without** version, so the two versions of `slack-026` share a display name),
`author_source_id`, `author_name`, `author_email`, `body`, `sent_at`, `version_at`, `thread_root_id`, `source_url`.

### `TeamsMeeting`

Key `(source_instance, meeting_id)`. Properties: `source_instance`, `meeting_id`, `name`, `display_name` (=
`meeting_id`), `title`, `started_at`, `ended_at`, `participants_raw`, `source_url`. Not embedded (its title is in
every segment's text).

### `TeamsTranscriptSegment`

Key `(source_instance, meeting_id, segment_id)`. Properties: `source_instance`, `meeting_id`, `segment_id`, `name`,
`display_name` (= `segment_id`), `sequence_number`, `speaker_source_id`, `speaker_name`, `start_offset_ms`,
`end_offset_ms`, `body`. No time of its own: layers use the meeting's `started_at`. No `NEXT_SEGMENT` relationship;
order is `sequence_number`.

### `Issue`

Key `(source_instance, issue_id)`. Identity from `issues` plus the **latest** `issue_versions` row. Properties:
`source_instance`, `issue_id`, `issue_key`, `name`, `display_name` (= `issue_key`), `issue_type`, `title`,
`description`, `acceptance_criteria`, `status`, `priority`, `creator_name`, `creator_source_id`, `assignee_name`,
`assignee_source_id`, `created_at`, `version_at`, `version_number` (latest), `version_count`, `versions_raw` (all
versions, summary fields), `comment_count`, `comments_raw` (latest version of each comment), `source_url`. Not embedded
(its versions are).

### `IssueVersion`

Key `(source_instance, issue_id, version_number)`. Properties: `source_instance`, `issue_id`, `version_number`, `name`,
`display_name` (`KV-7 v3`), `issue_type`, `title`, `description`, `acceptance_criteria`, `status`, `priority`,
`assignee_source_id`, `assignee_name` (absent when NULL), `changed_by_id`, `changed_by_name`, `version_at`,
`source_url`.

### `IssueComment`

Latest version of each comment. Key `(source_instance, comment_id)`. Properties: `source_instance`, `comment_id`,
`name`, `display_name` (= `comment_id`), `issue_id`, `author_source_id`, `author_name`, `body`, `created_at`,
`version_at`, `version_number`, `version_count`, `reply_to_comment_id` (absent when NULL), `source_url`.

### `Document`

Latest version of a document. Key `(source_instance, document_id)`. Properties: `source_instance`, `document_id`,
`name`, `display_name` (= `document_id`), `document_type`, `title`, `body`, `content_format`, `author_name`,
`author_source_id`, `created_at`, `version_at`, `version_number`, `change_summary`, `versions_raw` (**earlier**
versions only), `source_url`. Not embedded (its versions are). Its title's leading identifier (`REQ-VAT`) is what text
references resolve to.

### `DocumentVersion`

Key `(source_instance, document_id, version_number)`. Properties: `source_instance`, `document_id`, `version_number`,
`name`, `display_name` (`doc-001 v2`), `document_type`, `title`, `body`, `content_format`, `author_source_id`,
`author_name`, `created_at`, `version_at`, `change_summary`, `source_url`.

### `PullRequest`

Latest version of a PR. Key `(source_instance, repository, pr_number)`. Properties: `source_instance`, `repository`,
`pr_number`, `name`, `display_name` (`kvitta-api#58`), `title`, `description`, `author_name`, `author_source_id`,
`state`, `created_at`, `version_at`, `version_number`, `base_commit`, `head_commit`, `code_changes_raw` (latest
version's array), `reviews_raw` (latest version of each review), `source_url`.

### `PullRequestReview`

Latest version of each review entry. Key `(source_instance, repository, pr_number, source_id)`. Properties:
`source_instance`, `repository`, `pr_number`, `source_id`, `name`, `display_name` (`kvitta-api#60 review-030`),
`entry_type`, `version_number`, `version_count`, `pr_version_number`, `reviewed_commit`, `author_source_id`,
`author_name`, `body`, `created_at`, `version_at`, `reply_to_source_id`, `review_group_id`, `file_path`,
`line_number`, `diff_side` (the last five absent when NULL), `source_url`.

### `CodeChange`

One node per element of `pr_versions.code_changes`, for **every** PR version. Key
`(source_instance, repository, pr_number, version_number, file_path)`. Properties: `source_instance`, `repository`,
`pr_number`, `version_number`, `file_path`, `name`, `display_name` (`kvitta-api#58 app/integrations/fortnox/client.py`,
without the version; several nodes can share it), `change_type`, `before_summary`, `after_summary`, `diff`.

### `Person`

Key `person_key`. Written by every import from the identity registry (`docs/SQL_DATA_HANDOFF.md`, section 7).
Properties: `person_key`, `name`, `email`, `source_id` (absent when none), `emails`, `source_ids`, `names` (lists),
`identity_confidence` (`strong`/`weak`), `identity_ambiguous` (bool), `actor_type` (`person`/`mailbox`). Layer 6 adds
`collab_weighted_degree`, `collab_betweenness`, `community_id`, `algorithms_generated_by`, `algorithms_generated_at`
to eligible persons; layer 7 adds the embedding properties to eligible persons.

**Eligible person** (used by layers 5, 6, 7 and the agent): `actor_type <> 'mailbox'` and
`identity_ambiguous <> true`. Today 8 of 10 (the two mailboxes `support@kvitta.se` and `alerts@kvitta.se` are
excluded).

## 5. Derived nodes (written by the layers)

Every derived node has `derived = true`, `generated_by`, `generated_at`, `display_name`. LLM-derived nodes also have
`model`.

| Label | Layer (`generated_by`) | Key | Other properties |
| --- | --- | --- | --- |
| `Topic` | Knowledge (`topic-event-extraction-v1`) | `slug` | `name`, `topic_type` (`requirement`, `defect`, `incident`, `decision`, `other`), `summary`; layer 6 adds `bus_factor`, `expert_count`, `top_expert`, `algorithms_generated_*` |
| `Event` | Knowledge | `(topic_slug, slug)` | `name`, `event_type` (`created`, `decided`, `blocked`, `changed`, `resolved`, `regressed`, `other`), `occurred_at` (ISO string from the evidence), `summary` |
| `Repository` | Architecture (`architecture-layer-v1`) | `(source_instance, name)` | none beyond the stamp |
| `Module` | Architecture | `(source_instance, repository, path)` | `path` = directory of a file path, `(root)` for top level; `display_name` `<repository>:<path>` |
| `File` | Architecture | `(source_instance, repository, path)` | `file_name`, `extension`, `change_count` (number of `CodeChange` nodes on it) |
| `Component` | Architecture | `(source_instance, repository, slug)` | `name`, `component_type` (`service`, `endpoint`, `job`, `data_store`, `library`, `ui`, `external_system`, `other`), `summary`; layer 6 adds `bus_factor`, `expert_count`, `top_expert`, `depends_on_count`, `depended_on_by_count`, `affected_event_count`, `algorithms_generated_*` |
| `RootCause` | Root cause & impact (`causal-layer-v1`) | `slug` | `name`, `cause_type` (`requirement_gap`, `design_decision`, `implementation_gap`, `missing_test`, `process`, `external`, `other`), `summary` |
| `Expertise` | Expertise & collaboration (`collaboration-layer-v1`) | `(person_key, subject_label, subject_key)` | `score`, `share`, `rank`, `activity_count`, `first_activity_at`, `last_activity_at`; `subject_label` `Topic` or `Component`; `subject_key` = topic slug or `<source_instance>/<repository>/<component slug>`; `display_name` `<person> – <subject>` |
| `Community` | Graph algorithms (`graph-algorithms-v1`) | `community_id` (`collab-1`, ...) | `size` |
| `EmbeddingChunk` | Embeddings (`embedding-v3`) | none (found by `generated_by STARTS WITH "embedding-"`) | `chunk_of_label`, `chunk_index`, `chunk_count`, embedding properties; also labelled `Searchable` |
| `PipelineState` | every stage | `id = "singleton"` | section 8 |

Full semantics per label: the matching layer document.

## 6. Embedding properties

Written by layer 7 on 15 labels (`MailMessage`, `SlackMessage`, `TeamsTranscriptSegment`, `IssueVersion`,
`IssueComment`, `DocumentVersion`, `PullRequest`, `PullRequestReview`, `CodeChange`, `Topic`, `Event`, `Component`,
`RootCause`, eligible `Person`, `Community`), together with the label `Searchable`: `embedding` (1536 floats),
`embedding_text`, `embedding_model`, `embedding_source_hash`, `embedding_version`, `embedding_group`,
`embedding_is_latest`, `embedding_parts`, `embedded_at`. Not embedded: `Issue`, `Document`, `TeamsMeeting`,
`Repository`, `Module`, `File`, `Expertise`, `PipelineState`, mailbox and ambiguous persons. Details:
`docs/EMBEDDING_LAYER_HANDOFF.md`.

## 7. Relationships

### 7.1 Source relationships (import)

No properties unless listed. Counts from 2026-09-29.

| Type | From | To | Created when | Count |
| --- | --- | --- | --- | ---: |
| `SENT_MAIL` | `Person` | `MailMessage` | sender resolves | 14 |
| `MAIL_RECIPIENT` {`recipient_type`} | `MailMessage` | `Person` | each resolvable recipient | 31 |
| `SENT_SLACK_MESSAGE` | `Person` | `SlackMessage` | author resolves (every version) | 50 |
| `SLACK_THREAD_REPLY_TO` | `SlackMessage` (reply, every version) | `SlackMessage` (root, every version existing at import) | `thread_root_id` set and not the message itself | 31 |
| `PARTICIPATED_IN_MEETING` | `Person` | `TeamsMeeting` | each resolvable participant, **and** each resolvable speaker | 57 |
| `HAS_TEAMS_TRANSCRIPT_SEGMENT` | `TeamsMeeting` | `TeamsTranscriptSegment` | always | 45 |
| `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` | `Person` | `TeamsTranscriptSegment` | speaker resolves | 45 |
| `CREATED_ISSUE` | `Person` | `Issue` | creator resolves | 12 |
| `OWNS_ISSUE` | `Person` | `Issue` | latest assignee, else creator, resolves | 12 |
| `COMMENTED_ON_ISSUE` | `Person` | `Issue` | once per distinct resolvable comment author | 14 |
| `HAS_ISSUE_VERSION` | `Issue` | `IssueVersion` | always | 34 |
| `NEXT_ISSUE_VERSION` | `IssueVersion` | `IssueVersion` | between neighbours in version order | 22 |
| `CHANGED_ISSUE_VERSION` | `Person` | `IssueVersion` | `changed_by` resolves | 34 |
| `HAS_ISSUE_COMMENT` | `Issue` | `IssueComment` | always | 17 |
| `WROTE_ISSUE_COMMENT` | `Person` | `IssueComment` | author resolves | 17 |
| `REPLY_TO_ISSUE_COMMENT` | `IssueComment` | `IssueComment` | `reply_to_comment_id` set and parent already imported | 1 |
| `AUTHORED_DOCUMENT` | `Person` | `Document` | latest version's author resolves | 6 |
| `HAS_DOCUMENT_VERSION` | `Document` | `DocumentVersion` | latest author resolves (else no versions at all) | 10 |
| `NEXT_DOCUMENT_VERSION` | `DocumentVersion` | `DocumentVersion` | between neighbours | 4 |
| `AUTHORED_DOCUMENT_VERSION` | `Person` | `DocumentVersion` | that version's author resolves | 10 |
| `AUTHORED_PR` | `Person` | `PullRequest` | latest version's author resolves | 14 |
| `REVIEWED_PR` | `Person` | `PullRequest` | once per distinct resolvable review author (including the PR author when replying) | 26 |
| `HAS_PR_REVIEW` | `PullRequest` | `PullRequestReview` | always | 33 |
| `WROTE_PR_REVIEW` | `Person` | `PullRequestReview` | author resolves | 33 |
| `REPLY_TO_PR_REVIEW` | `PullRequestReview` | `PullRequestReview` | `reply_to_source_id` set, same PR, parent already imported | 6 |
| `HAS_CODE_CHANGE` | `PullRequest` | `CodeChange` | every element of every version | 39 |

(`SLACK_THREAD_REPLY_TO` is 31 for 27 reply rows because each reply to `slack-026` links to both of its versions.
`PARTICIPATED_IN_MEETING` is 57, the sum of the participant lists; every speaker is also a listed participant.)

### 7.2 Derived relationships (layers)

All have `derived = true` and `generated_by` (or `extracted_by`) and `generated_at` (or `extracted_at`), plus the
listed properties.

| Type | From | To | Layer | Extra properties | Count |
| --- | --- | --- | --- | --- | ---: |
| `MENTIONS_ISSUE` | any scanned source node | `Issue` | 1 References | `matched_text`, `source_property` | 175 |
| `MENTIONS_PULL_REQUEST` | any scanned source node | `PullRequest` | 1 | same | 62 |
| `MENTIONS_DOCUMENT` | any scanned source node | `Document` | 1 | same | 56 |
| `ABOUT_TOPIC` | `Issue` | `Topic` | 2 Knowledge | | 12 |
| `DERIVED_FROM` | `Topic` | every node of the issue's evidence bundle | 2 | | 388 |
| `EVENT_OF_TOPIC` | `Event` | `Topic` | 2 | | 51 |
| `EVIDENCED_BY` | `Event` | cited source node | 2 | | 159 |
| `ACTED_IN_EVENT` | `Person` | `Event` | 2 | | 96 |
| `CAUSED` | `Event` | `Event` (same topic) | 2 | `explanation`, `evidence` (list of identifiers) | 27 |
| `CONTAINS_MODULE` | `Repository` | `Module` | 3 Architecture | | 13 |
| `CONTAINS_FILE` | `Module` | `File` | 3 | | 23 |
| `MODIFIES_FILE` | `CodeChange` | `File` | 3 | | 39 |
| `PART_OF_REPOSITORY` | `Component` | `Repository` | 3 | | 18 |
| `IMPLEMENTED_IN` | `Component` | `File` | 3 | | 17 |
| `DEPENDS_ON` | `Component` | `Component` (same repository) | 3 | `dependency_type` (`calls`, `reads_from`, `writes_to`, `shares_logic_with`, `depends_on`), `explanation`, `evidence` | 13 |
| `COMPONENT_EVIDENCED_BY` | `Component` | cited source node | 3 | | 65 |
| `HAS_ROOT_CAUSE` | `Event` | `RootCause` | 4 Root cause & impact | `explanation`, `evidence` | 22 |
| `ROOT_CAUSE_IN_COMPONENT` | `RootCause` | `Component` | 4 | | 24 |
| `ROOT_CAUSE_EVIDENCED_BY` | `RootCause` | cited source node | 4 | | 50 |
| `CONTRIBUTED_TO` | `CodeChange` | `Event` | 4 | `contribution_type` (`introduced`, `resolved`, `partially_resolved`, `related`), `explanation`, `evidence` | 31 |
| `AFFECTED_COMPONENT` | `Event` | `Component` | 4 | `explanation`, `evidence` | 53 |
| `CROSS_TOPIC_CAUSED` | `Event` | `Event` (other topic) | 4 | `explanation`, `evidence` | 6 |
| `HAS_EXPERTISE` | `Person` | `Expertise` | 5 Expertise & collaboration | | 109 |
| `EXPERTISE_IN` | `Expertise` | `Topic` or `Component` | 5 | | 109 |
| `EXPERTISE_EVIDENCED_BY` | `Expertise` | each counted activity node | 5 | | 580 |
| `WORKS_WITH` | `Person` | `Person` (once per pair, from the smaller `person_key`) | 5 | `weight`, `shared_work_items` (max 50 names), `work_item_types` | 24 |
| `MEMBER_OF_COMMUNITY` | `Person` | `Community` | 6 Graph algorithms | | 8 |
| `CHUNK_OF` | `EmbeddingChunk` | its source node | 7 Embeddings | | 4 |

## 8. `PipelineState`

One node `(:PipelineState {id: "singleton"})`, created by the first stage that runs. It holds one timestamp per stage
and two embedding markers. Excluded from `/api/graph`.

| Property | Written by | Current value |
| --- | --- | --- |
| `last_import_at` | every SQL import | `2026-09-29T17:15:20.511655+00:00` |
| `last_extraction_at` | Reference extraction | `2026-09-29T17:28:14.292527+00:00` |
| `last_layer_build_at` | Knowledge layer | `2026-09-29T17:47:24.549087+00:00` |
| `last_architecture_build_at` | Architecture layer | `2026-09-29T17:53:42.692863+00:00` |
| `last_causal_build_at` | Root cause & impact layer | `2026-09-29T18:55:47.397134+00:00` |
| `last_collaboration_build_at` | Expertise & collaboration layer | `2026-09-29T19:07:23.811324+00:00` |
| `last_algorithms_run_at` | Graph algorithms | `2026-09-29T19:21:59.242785+00:00` |
| `last_embedding_at` | Embeddings | `2026-09-29T20:04:26.393727+00:00` |
| `last_embedding_failures` | Embeddings | `0` |
| `embedding_config` | Embeddings | fingerprint of the embedding configuration |

Staleness is computed from these timestamps (`docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 4).

## 9. Constraints and indexes

### 9.1 Uniqueness constraints (22)

Each constraint also creates a RANGE index of the same name. Created with `IF NOT EXISTS` by the import or the layer
that owns the label.

| Constraint | Label | Properties | Created by |
| --- | --- | --- | --- |
| `person_key` | `Person` | `person_key` | import |
| `mail_message_key` | `MailMessage` | `source_instance`, `message_id` | import |
| `slack_message_key` | `SlackMessage` | `source_instance`, `workspace_id`, `channel_id`, `message_id`, `version_number` | import |
| `teams_meeting_key` | `TeamsMeeting` | `source_instance`, `meeting_id` | import |
| `transcript_segment_key` | `TeamsTranscriptSegment` | `source_instance`, `meeting_id`, `segment_id` | import |
| `issue_node_key` | `Issue` | `source_instance`, `issue_id` | import |
| `issue_version_key` | `IssueVersion` | `source_instance`, `issue_id`, `version_number` | import |
| `issue_comment_key` | `IssueComment` | `source_instance`, `comment_id` | import |
| `document_node_key` | `Document` | `source_instance`, `document_id` | import |
| `document_version_key` | `DocumentVersion` | `source_instance`, `document_id`, `version_number` | import |
| `pull_request_key` | `PullRequest` | `source_instance`, `repository`, `pr_number` | import |
| `pull_request_review_key` | `PullRequestReview` | `source_instance`, `repository`, `pr_number`, `source_id` | import |
| `code_change_key` | `CodeChange` | `source_instance`, `repository`, `pr_number`, `version_number`, `file_path` | import |
| `topic_slug` | `Topic` | `slug` | Knowledge |
| `event_key` | `Event` | `topic_slug`, `slug` | Knowledge |
| `repository_key` | `Repository` | `source_instance`, `name` | Architecture |
| `module_key` | `Module` | `source_instance`, `repository`, `path` | Architecture |
| `file_key` | `File` | `source_instance`, `repository`, `path` | Architecture |
| `component_key` | `Component` | `source_instance`, `repository`, `slug` | Architecture |
| `root_cause_slug` | `RootCause` | `slug` | Root cause & impact |
| `expertise_key` | `Expertise` | `person_key`, `subject_label`, `subject_key` | Expertise & collaboration |
| `community_key` | `Community` | `community_id` | Graph algorithms |

### 9.2 Search and lookup indexes (owned by the Embeddings layer)

| Index | Type | On | Config |
| --- | --- | --- | --- |
| `searchable_embedding` | VECTOR | `:Searchable(embedding)` | 1536 dimensions, cosine, HNSW m 16, ef_construction 100, quantization BINARY (server default) |
| `searchable_text` | FULLTEXT | `:Searchable(embedding_text)` | analyzer `standard-no-stop-words` |
| `entity_lookup` | FULLTEXT | `Person`, `Issue`, `Document`, `PullRequest`, `Topic` on `name`, `display_name`, `title` | same analyzer |
| `issue_key_lookup` | FULLTEXT | `Issue.issue_key` | same analyzer |

Plus Neo4j's two built-in LOOKUP indexes (nodes, relationships). All ONLINE.

## 10. Graph API and graph panel

### 10.1 `GET /api/graph?source=<filter>`

Implemented by `load_neo4j_graph` in `backend/app.py`. `All` returns every node except `PipelineState` and every
relationship. Any other filter returns only the relationships of the listed types and the nodes at either end of them.
An unknown filter answers 400.

| Filter (button) | Relationship types |
| --- | --- |
| `All` (`Full graph`) | everything |
| `Mail` | `SENT_MAIL`, `MAIL_RECIPIENT` |
| `Slack` | `SENT_SLACK_MESSAGE`, `SLACK_THREAD_REPLY_TO` |
| `Teams` | `PARTICIPATED_IN_MEETING`, `HAS_TEAMS_TRANSCRIPT_SEGMENT`, `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` |
| `Issues` | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE`, `HAS_ISSUE_VERSION`, `NEXT_ISSUE_VERSION`, `CHANGED_ISSUE_VERSION`, `HAS_ISSUE_COMMENT`, `WROTE_ISSUE_COMMENT`, `REPLY_TO_ISSUE_COMMENT` |
| `Docs` | `AUTHORED_DOCUMENT`, `HAS_DOCUMENT_VERSION`, `NEXT_DOCUMENT_VERSION`, `AUTHORED_DOCUMENT_VERSION` |
| `PRs` | `AUTHORED_PR`, `REVIEWED_PR`, `HAS_PR_REVIEW`, `WROTE_PR_REVIEW`, `REPLY_TO_PR_REVIEW`, `HAS_CODE_CHANGE` |
| `References` | `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST`, `MENTIONS_DOCUMENT` |
| `Knowledge` | `ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED`, `ACTED_IN_EVENT` |
| `Architecture` | `CONTAINS_MODULE`, `CONTAINS_FILE`, `MODIFIES_FILE`, `IMPLEMENTED_IN`, `PART_OF_REPOSITORY`, `DEPENDS_ON`, `COMPONENT_EVIDENCED_BY` |
| `Causal` (button `Causes`) | `CAUSED`, `CROSS_TOPIC_CAUSED`, `HAS_ROOT_CAUSE`, `ROOT_CAUSE_IN_COMPONENT`, `ROOT_CAUSE_EVIDENCED_BY`, `CONTRIBUTED_TO`, `AFFECTED_COMPONENT` |
| `Collaboration` (button `Expertise`) | `HAS_EXPERTISE`, `EXPERTISE_IN`, `EXPERTISE_EVIDENCED_BY`, `WORKS_WITH` |
| `Algorithms` | `MEMBER_OF_COMMUNITY` |
| `Embeddings` (button `Chunks`, bottom row) | `CHUNK_OF` |

Response: `{"source", "nodes": [{"id", "label", "type", "summary", "properties"}], "relationships": [{"id",
"source", "target", "label", "sourceType", "properties"}]}`. `type` is the first label that is not `Searchable`;
`sourceType` is the filter the relationship type belongs to. `CAUSED` is in both `Knowledge` and `Causal`, and
`sourceType` reports the first (`Knowledge`).

Node properties are read with `n { .*, embedding: size(n.embedding) }` (`NODE_PROPERTIES`): the panel gets every
property except the embedding vector, and in its place the text `[1536 numbers, not loaded in the graph view]`. The
full graph response is about 2.2 MB and takes about 3 seconds on the Kvitta data (Knowledge 1.0 MB, References 0.5
MB). Search and the agent read the vectors directly from Neo4j.

### 10.2 The graph panel (left box)

`GraphView` in `frontend/src/main.tsx` draws the response with Cytoscape. Filter buttons across the top; the bottom
row holds `Chunks` and `Entry points` on the left and the SQL viewer button and Neo4j status on the right.

- **Layout:** every filter except Knowledge is placed by Cytoscape's force-directed `cose` layout, run in steps
  (`animate: true`, `refresh: 10`, `numIter: 400`, `randomize: true`), so the page stays responsive while the nodes
  settle. While it runs the panel shows `Drawing graph (N nodes): X %...`; before that, while the data is fetched,
  `Loading graph...`. On the full Kvitta graph (542 nodes) the layout takes about 12 seconds; the smaller filters are
  quicker. A new filter stops a layout still running.
- **Knowledge filter:** fixed positions, no force layout. Topics in a column on the left, events in columns on the
  right, and their evidence in a grid between them; the grid is made about as wide as it is high and the side columns
  as tall as the grid, so the whole view fits the panel (about 2 650 x 1 750 on the Kvitta data).
- **Distance slider:** spreads the force layout (node repulsion and edge length). It applies when released, and then
  the layout runs once.
- **Zoom:** from 0.1 to 2.5.
- **Colours:** one fixed colour per node type (`nodeColors`), including `Topic` (navy), `Event` (pink) and
  `EmbeddingChunk` (grey); the legend shows the types in view.
- **Entry points** (toggle): rings every embedded node (any node with `embedding_model`) in the shown filter, so the
  AI's search entry points are visible. Display only.
- **Motion:** on from the start. The whole graph turns slowly as one picture (`GRAPH_MOTION_TURNS = true`,
  `GRAPH_TURN_SECONDS = 120` per turn at speed 1) once the layout has placed the nodes. With the pointer over the
  graph it turns back upright and stands still so clicks land correctly; it also stands still while paused, while
  labels are shown and at speed 0. `Auto fit` zooms out to the whole visible graph (`GRAPH_AUTO_FIT_MS = 450`) before
  turning. Turning is a CSS rotation of the canvas, so it costs almost nothing.
- **Citations from the chat** ring and centre the cited node and hold the graph still until the pointer has been in
  the graph box and left it.
- Every node and every relationship of the chosen filter is sent to the browser and drawn. For much larger data see
  `docs/SCALING_HANDOFF.md`. Change the panel carefully and in isolation.

## 11. What is not modelled as graph structure

- Mail replies (`in_reply_to_id`) and transcript order (`sequence_number`) are properties, not relationships.
- Commits are properties (`base_commit`, `head_commit`, `reviewed_commit`), not nodes.
- Earlier versions of issue comments and PR reviews exist only in SQL (and in the parents' raw JSON).
- `MENTIONS_*` means "this text names that entity", nothing more: not implementation, causality, ownership or
  dependency. Interpretation starts in the Knowledge layer.
- Topics are built per issue. A PR or document that no issue connects to has no topic.
