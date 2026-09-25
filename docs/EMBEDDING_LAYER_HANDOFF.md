# Embedding Layer Handoff

This document describes the Embedding layer: the last step in the `Build graph layers` tab. It turns the knowledge of
every earlier layer into searchable vectors, so that a question can enter the graph at the right place no matter which
layer holds the answer.

**Status (2026-09-24):** `embedding-v3` is implemented. It keeps the v2 design and adds what larger data needs: caps
on every assembled list, chunking of long texts, batches sized by text length, API retries, and a version group and
latest flag on every embedded node, plus a paged, filterable text table and a run preview in the tab. The first v3 run
was made on 2026-09-24 (section 2).

The layer is implemented in `backend/embedding_pass.py`, exposed by `backend/app.py`, and displayed in `EmbeddingPanel`
inside `frontend/src/main.tsx`.

## 1. Purpose and Principle

Embeddings are the **entry points** into the graph. A question is embedded, compared to the stored vectors, and the
closest nodes are returned. From there, relationships lead to everything else. A node with no vector can only be
reached by walking to it from some other node. If no path is taken, it is never found.

Principle: **every layer gets at least one entry point.**

- Free text is embedded with its context, not as a bare body.
- Knowledge that exists only as relationships or numbers (causal explanations, dependencies, expertise shares,
  collaboration weights, community membership, bus factor) is written into the text of the nodes it describes before it
  is embedded.
- The embedding pass **only reads** what the other layers built. It never changes their nodes, relationships or
  properties. On their nodes it writes only its own properties (section 6) and the `Searchable` label; the only nodes
  it creates are its own `EmbeddingChunk` nodes (section 5.7).
- It must work for the current small dataset and for much larger data without redesign (section 5.0, 5.7, 8).

The design is based on the graph layers, not on how any particular AI agent uses them.

## 2. Current Local State

From `/api/embeddings`, right after the first v3 run:

| Field | Current value |
| --- | --- |
| Eligible / embedded / outdated / missing | 79 / 79 / 0 / 0 (all 15 labels fully embedded) |
| Chunks | 0 (no text is long enough to be split; the longest is 1 110 characters) |
| Version / model | `embedding-v3`, `text-embedding-3-large`, 1536 dimensions |
| Indexes | `searchable_embedding`, `searchable_text`, `entity_lookup`, `issue_key_lookup`, all `ONLINE` |
| Excluded persons | `Support` (mailbox), `Anna` (ambiguous identity) |
| `last_embedding_at` | `2026-09-24T21:17:26.224045+00:00` |
| `needs_rerun` / `stale_reasons` | `false` / `[]` |
| Preview of the next run | 0 nodes, 0 tokens (everything up to date) |
| Size of a full run | 45 887 characters, about 11 500 tokens |

Fulltext spot checks on `searchable_text` (after the v2 run; the texts are the same in v3): `"AUTH-19"` returns PR
`backend-api#47`, `slack-010`, `comment-005`, the `mobile_refresh.py` code change, `Community` `collab-1`, the `Topic`
and an `Event`; `"mobile_refresh.py"` returns a review, an `Event`, the PR, the code change and the `Component`. So the
derived layers are reachable by exact words too.

History: `embedding-v1` embedded 12 labels with mostly bare text, used one vector index per label, and had lost the
vectors of `Topic`, `Event`, `Component` and `RootCause` when those layers were rebuilt. `embedding-v2` introduced the
design in this document; `embedding-v3` added what larger data needs, with the same texts.

Known issue outside this layer: the chat agent (`backend/langgraph_agent/tools.py`) does not start. It fails at import
with `KeyError: 'Component'`, and it also expects the v1 per-label vector indexes. The agent is planned to be rebuilt on
top of this layer, so it is left as-is.

## 3. Model and Configuration

| Setting | Value |
| --- | --- |
| Provider | **OpenAI** (Embeddings API, `client.embeddings.create`) |
| Model | **`text-embedding-3-large`** |
| Dimensions | **1536** (the model's native size is 3072; the API's `dimensions` parameter shortens it to 1536) |
| Similarity function | **cosine** |
| Batch size | At most `BATCH_MAX_INPUTS` = 100 texts and `BATCH_MAX_CHARS` = 200 000 characters per API call (OpenAI allows 2048 inputs and about 300 000 tokens per request) |
| Retries | `API_MAX_RETRIES` = 6, with the OpenAI client's exponential backoff on rate limits (429), server errors and timeouts; timeout 120 s per call |
| Chunking | Texts over `MAX_TEXT_CHARS` = 12 000 characters are split into windows of `CHUNK_BODY_CHARS` = 6 000 with `CHUNK_OVERLAP_CHARS` = 500 overlap (section 5.7). The model's limit is 8191 tokens per text. |
| Token estimate | characters / 4 (`CHARS_PER_TOKEN_ESTIMATE`), used only by the preview |
| Version | `embedding-v3` |

`OPENAI_API_KEY` is required. If it is missing, `POST /api/embeddings/build` returns `503`.

Cost: the current graph has about 80 eligible nodes of short text. The longest source text is 1110 characters
(`DocumentVersion`). A full run therefore costs a fraction of a cent. The OpenAI API is called only when the button is
pressed. `GET /api/embeddings` never calls it.

## 4. What Is Embedded

15 labels. Every node of these labels gets a vector, except `Person` nodes that are not eligible.

| Layer | Label | New in v2 | Current count |
| --- | --- | --- | ---: |
| Import (sources) | `MailMessage` | | 4 |
| | `SlackMessage` (every version) | | 12 |
| | `TeamsTranscriptSegment` | | 9 |
| | `IssueVersion` (every version) | | 7 |
| | `IssueComment` | | 5 |
| | `DocumentVersion` (every version) | | 3 |
| | `PullRequest` | | 2 |
| | `PullRequestReview` | | 6 |
| | `CodeChange` | yes | 7 |
| Knowledge | `Topic` | | 1 |
| | `Event` | | 10 |
| Architecture | `Component` | | 3 |
| Root cause & impact | `RootCause` | | 3 |
| Expertise & collaboration | `Person` (eligible only) | yes | 5 |
| Graph algorithms | `Community` | yes | 2 |

**Eligible `Person`:** the same rule as the Expertise & collaboration layer: not `actor_type = "mailbox"` and not
`identity_ambiguous = true`. Currently excluded: `Support` and `Anna`.

**Not embedded, and why:**

| Label | Reason |
| --- | --- |
| `Issue`, `Document` | Parent nodes. Their content is embedded once per version (`IssueVersion`, `DocumentVersion`); embedding the parent too would return duplicates. |
| `TeamsMeeting` | Only a title. The title is included in every segment's text. |
| `Repository`, `Module`, `File` | Only names and paths. They are included in `Component` and `CodeChange` texts and are found by the fulltext index. |
| `Expertise` | Numbers. Expertise is written into the `Person`, `Topic` and `Component` texts. |
| `PipelineState` | Bookkeeping. |

**Relationships** get no vectors of their own. Their explanations are written into the texts of the nodes they connect
(section 5). This keeps one kind of entry point (nodes) and one index.

Knowledge from the **Reference extraction** layer (`MENTIONS_*`) is included in every source text as a `Mentions:` line.

A text too long for the model is split, and its extra parts become `EmbeddingChunk` nodes (section 5.7). They are
`Searchable` too, but are not counted as labels in the coverage table; the tab counts them separately.

## 5. Text Assembly per Label

Every text is built from properties and relationships already in the graph. Rules:

- Lines are joined with newlines. A line whose value is empty is left out entirely.
- The first line always names the kind of node, so a vector carries what it is and not only what it says.
- Lists are sorted (by time, then name) so that the same graph always gives the same text and the same hash.
- Every list is capped (section 5.0).
- Markdown headings in document bodies are stripped (`## Background` -> `Background`), as today.
- `MENTIONS_*` edges are read only when `extracted_by = "reference-extraction-v1"`, as in every other layer. All other
  relationship types read here are created by exactly one layer, so no further filter is needed.
- Imported diffs store newlines as the two characters `\n`; they are turned into real newlines in the `CodeChange` text.
- Evidence identifiers follow `causal_layer.source_identifier`, with `CodeChange` as `<display_name> v<version_number>`.

### 5.0 Caps on assembled lists

A hub node (a central component, a very active person) can be linked to hundreds of things. Listing all of them would
make its vector vague ("about everything") and could exceed the model's limit. Every list therefore has a cap. When a
list is longer, the most important entries are kept and the text ends the list with `... and N more` (or a line
`... and N more <kind>`), so it still says how much there is. With the current data no list reaches its cap, so the
caps do not change any text.

| Constant | Cap | List | Kept when over the cap |
| --- | ---: | --- | --- |
| `MAX_RECIPIENTS` | 20 | Mail recipients | first by name |
| `MAX_MENTIONS` | 20 | `Mentions:` line | first by name |
| `MAX_FILES` | 20 | PR changed files, component files | first by name |
| `MAX_ISSUES` | 20 | Topic issues | first by name |
| `MAX_ACTORS` | 15 | Event actors | first by name |
| `MAX_CAUSAL_LINKS` | 10 | Event `Caused by` / `Led to`, each | first by name |
| `MAX_ROOT_CAUSES` | 10 | Event root causes, component root causes | first by name |
| `MAX_COMPONENT_LINKS` | 10 | Event affected components, root cause components | first by name |
| `MAX_CODE_LINKS` | 10 | Event contributing code | first by name |
| `MAX_EVIDENCE` | 20 | `Evidence:` line | first by name |
| `MAX_EXPERTS` | 10 | Topic / component experts | highest share |
| `MAX_DEPENDENCIES` | 10 | Component `Depends on` / `Used by`, each | first by name |
| `MAX_AFFECTING_EVENTS` | 15 | Component `Affected by events` | most recent |
| `MAX_EXPLAINED_EVENTS` | 15 | Root cause `Explains` | first by name |
| `MAX_EXPERTISE_LINES` | 10 | Person expertise | highest share |
| `MAX_WORKS_WITH` | 10 | Person `Works with` | highest weight |
| `MAX_COMMUNITY_PEERS` | 15 | Person community peers | first by name |
| `MAX_COMMUNITY_MEMBERS` | 30 | Community members | first by name |
| `MAX_COMMUNITY_WORK_ITEMS` | 20 | Community shared work items | first by name |

### 5.1 Source nodes (Import + Reference extraction)

Every source text ends with a mentions line built from outgoing `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST` and
`MENTIONS_DOCUMENT` (targets: `Issue.issue_key`, `PullRequest.display_name`, leading document identifier of
`Document.title`, for example `REQ-AUTH-SESSION`):

```
Mentions: AUTH-17, backend-api#42, REQ-AUTH-SESSION
```

| Label | Text template (properties in `{}`) |
| --- | --- |
| `MailMessage` | `Email from {sender_name} to {recipient names from MAIL_RECIPIENT}, {sent_at}` / `Subject: {subject}` / `In reply to: {subject of the in_reply_to_id mail}` / `{body}` |
| `SlackMessage` | `Slack message in #{channel_name} by {author_name}, {sent_at}, version {version_number} of {n}` / `Reply to: {first 200 chars of the thread root message}` / `{body}` |
| `TeamsTranscriptSegment` | `Meeting transcript: {TeamsMeeting.title}, {started_at}, speaker {speaker_name}` / `Previous line ({speaker}): {first 200 chars of the segment with sequence_number - 1}` / `{body}` |
| `IssueVersion` | `Issue {issue_key} version {version_number} of {n}, {version_at}` / `Status: {status}, priority: {priority}, assignee: {assignee_name}, changed by: {changed_by_name}` / `Title: {title}` / `Description: {description}` / `Acceptance criteria: {acceptance_criteria}` |
| `IssueComment` | `Comment on issue {issue_key} by {author_name}, {created_at}` / `Reply to: {first 200 chars of the parent comment}` / `{body}` |
| `DocumentVersion` | `Document {document identifier or document_id} ({document_type}) version {version_number} of {n} by {author_name}, {version_at}` / `Title: {title}` / `Change summary: {change_summary}` / `{body}` |
| `PullRequest` | `Pull request {repository}#{pr_number} by {author_name}, state {state}` / `Title: {title}` / `Description: {description}` / `Changed files: {distinct CodeChange.file_path}` |
| `PullRequestReview` | `Pull request review on {repository}#{pr_number} (PR version {pr_version_number}) by {author_name}, {created_at}` / `Type: {entry_type}` / `Location: {file_path}:{line_number} ({diff_side})` / `Reply to: {first 200 chars of the parent review}` / `{body}` |
| `CodeChange` | `Code change in {repository}#{pr_number} version {version_number}: {file_path} ({change_type})` / `Pull request: {PullRequest.title}` / `Before: {before_summary}` / `After: {after_summary}` / `Diff: {diff}` |

`n` (number of versions) and the `issue_key` of an `IssueVersion` / `IssueComment` are read through
`HAS_ISSUE_VERSION` / `HAS_ISSUE_COMMENT` from the parent `Issue`. The document identifier follows the Reference
extraction rule (leading token of `Document.title` before `:`).

### 5.2 Knowledge layer

`Topic`:

```
Topic: {name} ({topic_type})
Issues: {Issue.issue_key via ABOUT_TOPIC}
{summary}
Events: {count via EVENT_OF_TOPIC}, from {first occurred_at} to {last occurred_at}
Experts: {person} ({share as %}, rank {rank}), ...                    <- Expertise layer
Bus factor {bus_factor}; top expert {top_expert}.                     <- Graph algorithms
```

`Event`:

```
Event: {name} ({event_type}), {occurred_at}
Topic: {Topic.name}
Actors: {Person.name via ACTED_IN_EVENT}
{summary}
Caused by: {cause Event.name} - {CAUSED.explanation}                  <- one line per incoming CAUSED
Led to: {effect Event.name} - {CAUSED.explanation}                    <- one line per outgoing CAUSED
Root cause: {RootCause.name} - {HAS_ROOT_CAUSE.explanation}           <- Root cause & impact
Affected component: {Component.name} - {AFFECTED_COMPONENT.explanation}
Contributing code: {CodeChange.display_name} v{version_number} ({contribution_type}) - {CONTRIBUTED_TO.explanation}
Evidence: {identifiers of EVIDENCED_BY targets}
```

`CROSS_TOPIC_CAUSED` is included with the same `Caused by:` / `Led to:` lines when it exists (currently 0).

### 5.3 Architecture layer

`Component`:

```
Component: {name} ({component_type}) in repository {repository}
{summary}
Files: {File.path via IMPLEMENTED_IN}
Depends on: {Component.name} ({dependency_type}) - {DEPENDS_ON.explanation}
Used by: {Component.name} ({dependency_type}) - {DEPENDS_ON.explanation}
Affected by events: {Event.name via AFFECTED_COMPONENT}               <- Root cause & impact
Root causes located here: {RootCause.name via ROOT_CAUSE_IN_COMPONENT}
Experts: {person} ({share as %}, rank {rank}), ...                    <- Expertise layer
Bus factor {bus_factor}: {risk sentence}; top expert {top_expert}.    <- Graph algorithms
```

Risk sentence: `bus_factor = 0` -> `no one has recorded expertise`; `1` -> `knowledge risk, one person holds most of
the expertise`; `>= 2` -> `expertise is shared by {bus_factor} people`.

### 5.4 Root cause & impact layer

`RootCause`:

```
Root cause: {name} ({cause_type})
{summary}
Explains: {Event.name} - {HAS_ROOT_CAUSE.explanation}                 <- one line per event
Located in components: {Component.name via ROOT_CAUSE_IN_COMPONENT}
Evidence: {identifiers of ROOT_CAUSE_EVIDENCED_BY targets}
```

### 5.5 Expertise & collaboration layer + Graph algorithms

`Person` (eligible only):

```
Person: {name}
Activity: authored {n} PRs, wrote {n} PR reviews, {n} Slack messages, {n} emails, {n} issue comments,
          {n} document versions, spoke in {n} meeting segments, acted in {n} events
Expertise: {subject name} ({Topic|Component}): {share as %}, rank {rank}   <- one line per Expertise
Works with: {other person} (weight {weight}: {work_item_types}), ...        <- WORKS_WITH, both directions, by weight
Community: {community_id} with {other members}                              <- Graph algorithms
Collaboration network: weighted degree {collab_weighted_degree}, betweenness {collab_betweenness} ({role sentence})
```

Role sentence: `collab_betweenness >= 0.3` -> `bridges otherwise separate groups`; `> 0` -> `sometimes connects
others`; `0` -> `not a bridge between groups`. The thresholds are named constants in `embedding_pass.py`.

`Community`:

```
Community: {community_id}, {size} members (Louvain community detection on WORKS_WITH)
Members: {Person.name via MEMBER_OF_COMMUNITY}
Collaborate on: {distinct work_item_types of WORKS_WITH edges inside the community}
Shared work items: {distinct shared_work_items inside the community, max 20}
```

### 5.6 Example (real data, 2026-09-24)

```
Component: Mobile session refresh endpoint (endpoint) in repository backend-api
Refreshes sessions for the mobile client ...
Files: backend/auth/mobile_refresh.py
Depends on: Session lifetime policy (calls) - ...
Affected by events: Mobile administrators reported signed out after sixty minutes while active, ...
Root causes located here: Mobile refresh endpoint was omitted from the inactivity-policy implementation, ...
Experts: Erik Nilsson (62.5 %, rank 1), Anna Lindqvist (37.5 %, rank 2)
Bus factor 1: knowledge risk, one person holds most of the expertise; top expert Erik Nilsson.
```

### 5.7 Chunking long texts

The model reads at most 8191 tokens per text. A text longer than `MAX_TEXT_CHARS` (12 000 characters, well under the
limit even for dense text) is split by `split_text`:

- The first line (what the node is, for example `Document REQ-X (requirement) version 3 of 3 by ..., 2026-...`) is
  repeated at the top of every part, followed by `(Part i of n)`, so each part's vector still knows its source.
- The rest is cut into windows of `CHUNK_BODY_CHARS` (6 000) that overlap by `CHUNK_OVERLAP_CHARS` (500), so a sentence
  on a boundary is whole in at least one part. A window ends at the last line break or space in its final fifth when
  there is one.
- **Part 1** is embedded on the node itself (its `embedding_text` is part 1), so every node keeps exactly one vector
  and the coverage table stays one row per node.
- **Parts 2..n** become `EmbeddingChunk` nodes (section 6), linked `(:EmbeddingChunk)-[:CHUNK_OF]->(source)`.
  A search hit on a chunk leads to its source through `CHUNK_OF`.

With the current data no text exceeds 1 110 characters, so no chunks exist.

## 6. What Is Written to the Graph

Properties on each embedded node (the only properties this layer writes on other layers' nodes):

| Property | Meaning |
| --- | --- |
| `embedding` | The vector (1536 floats), written with `db.create.setNodeVectorProperty`. |
| `embedding_text` | The exact text that was embedded (section 5), or its part 1 when the text is chunked. Stored so the UI and later the agent can show what a vector represents, and so the fulltext index can search it. |
| `embedding_model` | `text-embedding-3-large`. |
| `embedding_source_hash` | SHA-256 of the full assembled text together with `embedding_group` and `embedding_is_latest`. |
| `embedding_version` | `embedding-v3`. |
| `embedding_group` | The source the node belongs to (table below). Several versions of one source share a group, so search can fold them into one hit. |
| `embedding_is_latest` | `true` when the node is its source's latest version (or the source has only one state). |
| `embedding_parts` | Number of parts the text was split into; 1 when not chunked. |
| `embedded_at` | Run timestamp. |

`embedding_group` per label:

| Label | Group | Latest when |
| --- | --- | --- |
| `MailMessage` | `Mail <message_id>` | always |
| `SlackMessage` | `Slack <message_id>` | highest `version_number` of that message |
| `TeamsTranscriptSegment` | `Teams <meeting_id>/<segment_id>` | always |
| `IssueVersion` | `Issue <issue_key>` | highest `version_number` of that issue |
| `IssueComment` | `Issue comment <comment_id>` | always (the node is the latest comment row) |
| `DocumentVersion` | `Document <document_id>` | highest `version_number` of that document |
| `PullRequest` | `Pull request <repository>#<pr_number>` | always |
| `PullRequestReview` | `Pull request review <repository>#<pr_number>/<source_id>` | always |
| `CodeChange` | `Code change <repository>#<pr_number> <file_path>` | highest PR version that changed that file |
| `Topic`, `Event`, `Component`, `RootCause`, `Person`, `Community` | `<Kind> <key>` | always |

Label: every embedded node also gets the extra label **`Searchable`**. A node that is no longer eligible (for example a
`Person` that became ambiguous) loses the label and all properties above.

**`EmbeddingChunk` nodes** (created only for chunked texts), labels `EmbeddingChunk:Searchable`:

| Property | Meaning |
| --- | --- |
| `display_name` | `<source display name> (part i of n)` |
| `chunk_of_label`, `chunk_index`, `chunk_count` | Source label and which part this is (2..n) |
| `embedding`, `embedding_text`, `embedding_model`, `embedding_version`, `embedding_group`, `embedding_is_latest`, `embedded_at` | As on the source node |
| `derived`, `generated_by` (= `EMBEDDING_VERSION`), `generated_at` | Standard layer stamp |

`(:EmbeddingChunk)-[:CHUNK_OF {derived, generated_by, generated_at}]->(source)`. A node's chunks are deleted and
recreated whenever the node is re-embedded, and deleted when the source is gone or no longer eligible. Queries find
chunk nodes by `generated_by STARTS WITH "embedding-"` rather than by label, so Neo4j does not warn while none exist.

The pass never writes `generated_by` on nodes it did not create, the same rule the Graph algorithms layer follows with
`algorithms_generated_by`.

Graph filter: `CHUNK_OF` is mapped to the filter key `Embeddings` in `backend/app.py` (`GRAPH_SOURCE_RELATIONSHIPS`).
The button is labelled **`Chunks`** (display name only, via `sourceFilterDisplayName`, like `Causes` for `Causal`;
the key sent to `/api/graph` stays `Embeddings`), since it shows only chunk nodes, not every embedded node. It sits in
the graph panel's bottom row, left-aligned (`graph-footer-left`), apart from the layer filters at the top; the SQL
Viewer button and the Neo4j status on the right are unchanged. With the current data it
shows an empty graph (checked: `/api/graph?source=Embeddings` returns 0 nodes, 0 relationships), since no chunks exist.

`Entry points` button (next to `Chunks`): a toggle that rings every embedded node (any node with `embedding_model`, chunks
included) in whatever filter is shown, so the entry points are seen in context. Rings show only while the button is
on. Display only; see `docs/GRAPH_DATA_HANDOFF.md` (Frontend Filter Mapping).

## 7. Indexes

| Index | Type | On | Purpose |
| --- | --- | --- | --- |
| `searchable_embedding` | vector, 1536, cosine | `:Searchable(embedding)` | **One index across all layers.** Scores from every label are directly comparable. Filtering by label is done on the returned nodes. |
| `searchable_text` | fulltext | `:Searchable(embedding_text)` | Exact words the vector can miss: issue keys, PR numbers, file names, person names. Together with the vector index this gives hybrid search. |
| `entity_lookup` | fulltext | `Person`, `Issue`, `Document`, `PullRequest`, `Topic` on `name`, `display_name`, `title` | Kept as-is: name resolution. |
| `issue_key_lookup` | fulltext | `Issue.issue_key` | Kept as-is. |

The 12 per-label vector indexes from `embedding-v1` (`mailmessage_embedding` ... `rootcause_embedding`) are dropped by
the first v2 run. All indexes are created with `IF NOT EXISTS`, so every run is idempotent.

## 8. Build / Rebuild Behavior

1. Require `last_algorithms_run_at` to exist (else `409 {"error": "Run Graph algorithms first."}`), since texts read
   from every layer.
2. Require `OPENAI_API_KEY` (else `503`).
3. Ensure the indexes (section 7); drop the v1 per-label vector indexes if present.
4. For each label, read the nodes, assemble their texts (section 5), their group and latest flag, and split long texts
   (section 5.7). Only reads.
5. Skip a node when `embedding_source_hash`, `embedding_model`, `embedding_version` and `embedding_parts` all match,
   unless `force = true`.
6. Embed every part of the remaining nodes in batches bounded by count and characters (section 3). The OpenAI client
   retries transient errors itself; a batch that still fails marks its nodes as failed and the run continues.
7. As soon as all parts of a node have a vector, write the node and replace its chunks in one transaction. A node with
   a failed part is not written, so it keeps its previous state.
8. Remove the layer's properties and label from nodes that are no longer eligible, and delete chunks whose source is
   gone or no longer eligible.
9. Update `PipelineState.last_embedding_at`, and this layer's two markers `last_embedding_failures` (number of failed
   nodes) and `embedding_config` (`config_fingerprint()`), which decide whether the tab may use the stored path
   (section 10).
10. Return the current state plus run metadata.

Memory stays bounded: vectors are held only until their node is written.

Because texts include data from other layers, rebuilding any layer changes some texts, and only those nodes are
re-embedded on the next run. Nodes recreated by a layer rebuild have no vector until the next run; the tab shows them as
missing.

## 9. Pipeline State and Staleness

`UPSTREAM_BY_STAGE["embeddings"]` in `backend/pipeline_staleness.py` changes from
`["import", "knowledge", "architecture", "causal"]` to:

```python
["import", "references", "knowledge", "architecture", "causal", "collaboration", "algorithms"]
```

because every one of those layers now contributes to the texts. Staleness propagation is otherwise unchanged (see
`GRAPH_DATA_HANDOFF.md`, "Pipeline Staleness").

## 10. Backend API

### `GET /api/embeddings`

| Field | Meaning |
| --- | --- |
| `model`, `provider`, `dimensions`, `similarity`, `version` | Configuration (section 3). |
| `models_in_use` | Distinct `embedding_model` values in the graph (warning if mixed). |
| `counts` | `eligible`, `embedded` (up to date), `outdated`, `missing`, summed over all labels, plus `chunks` (existing `EmbeddingChunk` nodes with a vector). |
| `per_label` | One row per label: `layer`, `label`, `total`, `eligible`, `embedded` (up to date), `outdated` (a vector exists but the hash, model, version or number of parts differs), `missing` (no vector, or no text to embed). |
| `indexes` | `name`, `type`, `labels`, `properties`, `state` of the four indexes in section 7, always all four. An index that does not exist yet is listed with its intended shape (`EXPECTED_INDEXES`) and `state = "NOT CREATED"`, shown in the tab as `Not created yet`. |
| `excluded_persons` | Person nodes left out, with reason (mailbox / ambiguous identity). |
| All eight pipeline timestamps, `needs_rerun`, `stale_reasons` | As in the other layers. |

The summary never returns node texts, so its size does not grow with the graph; texts come page by page from
`/api/embeddings/nodes`. The field `counted_from` says which of two paths produced the numbers:

- **`stored`** (fast): used when all three hold: the layer is not stale, the last run had no failures
  (`PipelineState.last_embedding_failures = 0`), and it ran with the current configuration
  (`PipelineState.embedding_config` equals `config_fingerprint()`). A text can only change when an upstream layer is
  rebuilt, which staleness already tracks, so the stored properties are then exact. The counts are plain Cypher
  counts on `embedding_version` / `embedding_model`; no text is assembled.
- **`assembled`** (exact): otherwise. Every text is assembled and compared with its stored hash, as the run does.

`config_fingerprint()` hashes everything besides the graph that decides the texts and their splitting: the version,
model, dimensions, every `MAX_*` cap, the chunk sizes, `QUOTE_CHARS` and `BRIDGE_BETWEENNESS`. A code change to a text
builder must still bump `EMBEDDING_VERSION`, since code itself is not part of the fingerprint.

Checked on 2026-09-24 (read-only): both paths give identical per-label counts and identical pages for ten
filter/offset combinations; warm, the stored summary takes about 70 ms against about 175 ms assembled for 79 nodes,
and the gap grows with the graph.

### `GET /api/embeddings/nodes`

One page of the embedded-texts table. Query parameters, all optional: `layer`, `label`, `status` (`current`,
`outdated`, `missing`; `missing` also returns `empty`), `offset` (default 0), `limit` (default 50, max 500).

On the `stored` path only the page's rows are read (Cypher `SKIP` / `LIMIT` per label, walking the labels in pipeline
order); on the `assembled` path only the labels matching `layer` / `label` are assembled. Both order rows by pipeline
label order, then display name, then element id, so the two paths return the same pages.

Returns `total` (rows matching the filter), `offset`, `limit`, `counted_from` and `nodes`: `layer`, `label`,
`display_name`, `text` (the text of the node's own vector: the whole text, or part 1 when it is chunked; on the
assembled path this is the text as assembled now), `group`, `is_latest`, `parts`, `status` (`current`, `outdated`,
`missing`, `empty`), `embedded_at`.

### `GET /api/embeddings/preview`

What the next run would embed. Query `force=true` previews `Re-embed all`. Reads only; never calls OpenAI and writes
nothing. Returns `forced`, `nodes`, `parts` (texts to embed, including chunks), `chunks`, `characters`,
`estimated_tokens` (characters / 4) and `per_label` (the same fields per label, only labels with work).

### `POST /api/embeddings/build`

Body `{"force": bool}`. Returns the summary plus `run_at`, `forced`, `embedded_now` (nodes embedded in this run),
`chunks_now` (chunk nodes written), `skipped_unchanged`, `skipped_empty`, `removed` (nodes that lost eligibility),
`removed_chunks`, `failed`, `failures` (`label`, `key` = display name, `error`), `per_label_run`, `token_usage`.

Writes go through `elementId`: the nodes are read and written in the same run, so no key properties are needed.

Errors: `409` if Graph algorithms has not run; `503` if `OPENAI_API_KEY` is missing; `500` otherwise.

## 11. Frontend UI

Tab `Embeddings` inside `BuildGraphLayersPanel`, rendered by `EmbeddingPanel`, seventh of seven inner tabs. It follows
the same pattern as the other layer tabs (see `SESSION_HANDOFF.md`, "Tab conventions").

Button row: `Build embeddings` / `Building embeddings...`, `Re-embed all` with its confirmation, and
`Preview next run` / `Previewing...` (calls `GET /api/embeddings/preview`; never calls OpenAI). Status row: last
embedding run and the timestamp of every upstream layer. The stale warning uses the generic stale text of the other
tabs and lists `stale_reasons`.

Below the button row:

| Element | Content |
| --- | --- |
| Description (`reference-description`) | `Turns what every layer knows into searchable vectors, so a question can enter the graph at the right place. Vectors are stored as properties on existing nodes.` |
| Model line (`reference-description`) | `Embedding model: text-embedding-3-large (OpenAI) · 1536 dimensions · cosine similarity`, read from the payload |
| Preview line (after `Preview next run`, `reference-success`) | `Next run: N nodes (P texts including C chunks), about T tokens. Nothing has been sent to OpenAI.`, or when there is nothing to do: `Next run: nothing to embed, every node is up to date. Build embeddings would not call OpenAI.` Cleared after a build. |
| Counts heading `Written to existing nodes:` | Embedded, Outdated, Missing, Total eligible |
| Counts heading `Nodes and relationships:` | Chunks (EmbeddingChunk, CHUNK_OF): the only nodes this layer creates |
| Counts heading `Indexes:` | State of the vector index `searchable_embedding` and the fulltext index `searchable_text` (`Not created yet` before the first run) |
| Run metrics (only right after a build, `justRan`) | Embedded now, Chunks now, Skipped, Removed (nodes + chunks), Failed, Tokens |

Tables, in order, each with a short caption (`knowledge-table-caption`):

| Table | Heading suffix | Columns |
| --- | --- | --- |
| `Coverage by layer` | `(written to existing nodes)` | Layer, Label (node label), Nodes, Eligible, Embedded, Outdated, Missing (counts centered) |
| `Embedded texts` | `(property: embedding_text)` | Layer, Label (node label), Node (property display_name), Text (property embedding_text; `embedding-text-cell`: 480px, `white-space: pre-wrap` so the text's lines show), Group (property embedding_group), Latest (property embedding_is_latest), Parts (property embedding_parts), Status (`Up to date`, `Outdated`, `Missing`, `No text`), Embedded at (property); height capped with `reference-table-wrapper-capped`. Loaded page by page from `GET /api/embeddings/nodes` (`EMBEDDING_PAGE_SIZE` = 50). Above the table, `embedding-table-controls`: Layer, Label and Status filters (changing one returns to the first page; the Label list follows the chosen layer), `Showing a–b of n`, Previous / Next. Two-block caption: what the text is; what Group, Latest and Parts mean. |
| `Indexes` | `(vector and fulltext)` | Name, Type, Used for (fixed text per index name, `embeddingIndexPurposes`), Label (node label), Property, State. Block caption (`div.knowledge-table-caption`) with three bold-labelled blocks: **Two kinds of index** (vector = meaning, fulltext = exact words), **Search** (searchable_embedding + searchable_text cover the same text in every layer; hybrid search), **Lookup** (entity_lookup and issue_key_lookup resolve names and keys, including Issue and Document, which are not embedded). |
| `Excluded persons` | `(existing Person nodes)` | Person (property), Person key (property), Reason (computed from properties) |
| `Failures from the last run` | | Label, Node, Error; shown only when there are failures |

Whichever of the last three tables is shown last gets `layer-last-table`. The panel uses `embedding-panel`, which is in
the `grid-auto-rows: max-content` group in `styles.css` together with the other many-table panels.

Empty state (nothing embedded or outdated): `No embeddings yet. Press the button to build them.` The coverage and text
tables are still shown, so the texts can be read before the first run.

## 12. Verification

### Checks done for v3 (2026-09-24)

| Check | Result |
| --- | --- |
| `split_text` on a 50 000-character text | 10 parts, each at most 12 000 characters, each starting with the repeated first line and `(Part i of n)`; every word covered |
| `_batches` with ten 50 000-character texts and 250 short ones | Every batch within 100 inputs and 200 000 characters; no piece lost |
| Caps (`_person_text` with 15 collaborators) | The 10 with the highest weight kept, `... and 5 more` added |
| `embedding_is_latest` on real data | Only `AUTH-17 v5` and `AUTH-19 v2` are latest among the issue versions |
| Texts unchanged by v3 | Preview reports the same 45 887 characters as v2 |
| `GET /api/embeddings`, `/nodes`, `/preview` through the Vite proxy | Correct answers; no Neo4j warnings in the backend log |
| `tsc --noEmit`, `py_compile`, `test_pipeline_staleness` | Pass |

### Chunk write and removal test (2026-09-24)

No text in the data is long enough to be split, so the real pass was run once from a throwaway script with the chunk
threshold lowered for that process only (`MAX_TEXT_CHARS` 600, `CHUNK_BODY_CHARS` 300, `CHUNK_OVERLAP_CHARS` 50; the
code was not changed). 9 468 tokens.

| Step | Result |
| --- | --- |
| Write | 29 nodes split, **84 `EmbeddingChunk` nodes** and 84 `CHUNK_OF` written, 0 failures |
| Chunk nodes | All `EmbeddingChunk:Searchable`, 1536-dimension vector, `embedding_text`, `generated_by = embedding-v3` |
| Consistency | 0 chunks without a source; 0 label or group mismatches with the source; 0 sources where `embedding_parts` differs from 1 + chunks or a `chunk_index` is missing |
| Search | Fulltext `"absolute ceiling"` returns chunk nodes (for example `AUTH-17 v4 (part 4 of 4)`) |
| Graph | The `Chunks` button showed 113 nodes and 84 `CHUNK_OF` |
| Removal | A normal `Build embeddings` (real threshold) re-embedded the 29 nodes as 1 part each and **removed all 84 chunks**; the graph is back to 79 embedded nodes, 0 chunks, `Full graph` 107 nodes / 488 relationships as before |

Two fixes came out of the test:

- **Status now checks the number of parts.** A node is `outdated` when its stored `embedding_parts` differs from how
  its text splits now, so a changed chunk size re-splits the node. Before, only the text hash was compared and the
  test chunks would have stayed.
- **`Searchable` is never a node type in the graph API.** Neo4j lists labels in the order their tokens were created,
  so `Searchable` came before `EmbeddingChunk` and chunk nodes showed as type `Searchable`. `load_neo4j_graph` in
  `backend/app.py` now skips `Searchable` when choosing a node's type and fallback label.

### Semantic search questions

These questions are embedded and searched against `searchable_embedding` (top 5). Each should return at least one of
the expected nodes. They cover one layer each.

**Run on 2026-09-24** (one embeddings call, 59 tokens; read-only search): vector search alone finds an expected node in
the top 5 for **5 of 7** questions, and with the fulltext index added (hybrid search) all **7 of 7** are answered:

| Question | Vector top hit (score) | Result |
| --- | --- | --- |
| Why was AUTH-17 blocked? | `Event` "AUTH-17 was blocked pending refresh-path implementation" (0.788) | pass, rank 1 |
| Which code changed for the mobile fix? | `Event` "Mobile refresh endpoint policy fix proposed" (0.708) | pass, the `CodeChange` `mobile_refresh.py` is rank 2 |
| What uses the session lifetime policy? | `Component` Session lifetime policy (0.792) | pass, rank 1 |
| What was the root cause of the mobile regression? | `RootCause` "Known mobile refresh-path risk was not followed up" (0.757) | pass, both mobile root causes are rank 1-2 |
| Who knows the most about the mobile refresh endpoint? | `Component` Mobile session refresh endpoint (0.798) | no `Person` in the top 5, but the rank-1 hit's text says `Experts: Erik Nilsson (62.5 %, rank 1)`, so the answer is found; fulltext (`expert* AND "mobile session refresh endpoint"`) returns the `Person` profiles first |
| Where is there a knowledge risk if someone leaves? | `Topic` (0.651) | vector miss: the bus factor sentence is one line in a long component text, so the vector is mostly about sessions. Fulltext `"knowledge risk"` returns exactly the `Component` with bus factor 1 |
| Which groups exist in the team? | `Community` collab-1 (0.728) | pass, both communities are rank 1-2 |

Conclusions for the agent that will use this layer:

- **Use hybrid search** (vector + `searchable_text`): the two indexes cover each other's misses, as designed.
- **Read the hit's text, not only its label.** A component or topic text often holds the answer about people and risk.
- **Ranking questions** ("where is the lowest bus factor", "who has the highest betweenness") are answered best with a
  direct query on the metric properties (`bus_factor`, `collab_betweenness`), not by search.
- `db.index.vector.queryNodes` works but is **deprecated** in the installed Neo4j version, which recommends the Cypher
  `SEARCH` clause instead. New search code should use `SEARCH`.

| Question | Expected node(s) |
| --- | --- |
| Why was AUTH-17 blocked? | `Event` (blocked), `TeamsTranscriptSegment` `seg-003`, `IssueComment` `comment-002` |
| Which code changed for the mobile fix? | `CodeChange` `backend-api#47 backend/auth/mobile_refresh.py` |
| What uses the session lifetime policy? | `Component` Session lifetime policy |
| What was the root cause of the mobile regression? | `RootCause` (implementation_gap) |
| Who knows the most about the mobile refresh endpoint? | `Person` Erik Nilsson |
| Where is there a knowledge risk if someone leaves? | `Component` with bus factor 1 |
| Which groups exist in the team? | `Community` `collab-1`, `collab-2` |

## 13. Important Boundaries

- Reads Neo4j; on other layers' nodes writes only the properties in section 6 and the `Searchable` label. Never
  touches PostgreSQL.
- Creates only its own `EmbeddingChunk` nodes and `CHUNK_OF` relationships, and deletes only those.
- Never creates, changes or deletes nodes or relationships of any other layer.
- Never writes `generated_by` on nodes it did not create.
- Calls the OpenAI Embeddings API only on `POST /api/embeddings/build`.
- Runs last; depends on every earlier layer having run at least once.

## 14. Code Map

| File | What it holds for this layer |
| --- | --- |
| `backend/embedding_pass.py` | Configuration and cap constants, one read query per label (`QUERIES`), one text builder per label, `GROUPS` (group and latest flag per label), `LABELS` (label, layer, builder), `split_text`, `_batches`, indexes, the stored path (`config_fingerprint`, `read_run_markers`, `_stored_per_label`, `_stored_page`) and the assembled path (`_assemble`, `_per_label`), `load_embedding_state`, `embedding_nodes_page`, `embedding_preview`, `run_embedding_pass`. |
| `backend/pipeline_staleness.py` | `UPSTREAM_BY_STAGE["embeddings"]` (section 9). |
| `backend/app.py` | `GET /api/embeddings`, `GET /api/embeddings/nodes`, `GET /api/embeddings/preview`, `POST /api/embeddings/build` (`409` / `503` / `500`); graph filter `Embeddings` -> `CHUNK_OF`; `load_neo4j_graph` skips the `Searchable` label when choosing a node's type. |
| `frontend/src/main.tsx` | `EmbeddingState`, `EmbeddingNodesPage`, `EmbeddingPreview` types and `EmbeddingPanel` (section 11); `Embeddings` in `DataSource` and its `Chunks` button in the graph footer; `propertyRows` lists the `embedding` property last in the graph selection panel (display only). |
| `frontend/src/styles.css` | `embedding-panel` in the many-table panel group; `embedding-text-cell`; `embedding-table-controls`; `graph-footer-left`. |

Adding a label: add a query to `QUERIES`, a text builder, a `GROUPS` entry and a `LabelConfig` row in `LABELS`.
Nothing else changes; the tab, the index and the staleness pick it up.
