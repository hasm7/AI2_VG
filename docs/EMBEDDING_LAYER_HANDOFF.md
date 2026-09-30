# Layer 7: Embeddings

The last graph-building step. It turns what every earlier layer knows into searchable vectors, so a question can
enter the graph at the right place whichever layer holds the answer. Each embedded node gets a text assembled from
its own properties **and its context from every layer**, a vector of that text, and the extra label `Searchable`,
which carries one vector index and one fulltext index across all layers.

**Verified on 2026-09-30** (Kvitta data) against `backend/embedding_pass.py`, `backend/pipeline_staleness.py`, `backend/app.py`,
`frontend/src/main.tsx`, the live graph and `GET /api/embeddings`.

Code: `backend/embedding_pass.py`. UI: `EmbeddingPanel`, tab `Embeddings` (seventh of seven).

## 1. Principle

Embeddings are the entry points into the graph: a question is embedded, compared with the stored vectors, and the
closest nodes are where the agent starts walking. A node without a vector can only be reached from another node.

- **Every layer gets at least one entry point.**
- Free text is embedded with its context, not as a bare body.
- Knowledge that exists only as relationships or numbers (causal explanations, dependencies, expertise shares,
  collaboration weights, communities, bus factor) is written into the text of the nodes it describes.
- The pass **only reads** other layers. On their nodes it writes only its own properties (section 6) and the
  `Searchable` label; the only nodes it creates are its own `EmbeddingChunk` nodes.
- It must work for much larger data: every list is capped, long texts are chunked, API calls are batched and retried.

## 2. Configuration

| Setting | Value |
| --- | --- |
| Provider, model | OpenAI Embeddings API, `text-embedding-3-large` |
| Dimensions | 1536 (the model's native 3072, shortened by the API's `dimensions` parameter) |
| Similarity | cosine |
| Version | `embedding-v3` |
| Batching | at most `BATCH_MAX_INPUTS` = 100 texts and `BATCH_MAX_CHARS` = 200 000 characters per call |
| Retries | `API_MAX_RETRIES` = 6 (client backoff on 429, 5xx, timeouts), `API_TIMEOUT_SECONDS` = 120 |
| Chunking | texts over `MAX_TEXT_CHARS` = 12 000 characters are split into windows of `CHUNK_BODY_CHARS` = 6 000 overlapping by `CHUNK_OVERLAP_CHARS` = 500 |
| Token estimate (preview only) | characters / 4 |
| Quote length | `QUOTE_CHARS` = 200 (quoted parent messages, previous transcript lines) |
| Bridge threshold | `BRIDGE_BETWEENNESS` = 0.3 |

`OPENAI_API_KEY` is required for a build (503 without it). Prerequisite: `last_algorithms_run_at` (else 409 `Run
Graph algorithms first.`). The API is called only by `POST /api/embeddings/build`; every GET is free.

## 3. What is embedded

15 labels, in pipeline order:

| Layer | Label | Count now |
| --- | --- | ---: |
| Import | `MailMessage` | 14 |
| | `SlackMessage` (every version) | 50 |
| | `TeamsTranscriptSegment` | 45 |
| | `IssueVersion` (every version) | 34 |
| | `IssueComment` | 17 |
| | `DocumentVersion` (every version) | 10 |
| | `PullRequest` | 14 |
| | `PullRequestReview` | 33 |
| | `CodeChange` | 39 |
| Knowledge | `Topic`, `Event` | 10, 51 |
| Architecture | `Component` | 18 |
| Root cause & impact | `RootCause` | 15 |
| Expertise & collaboration | `Person` (eligible only) | 8 of 10 |
| Graph algorithms | `Community` | 2 |

360 embedded nodes in total, plus 4 `EmbeddingChunk` nodes (section 5).

Not embedded: `Issue` and `Document` (their versions are; the parent would duplicate them), `TeamsMeeting` (its title
is in every segment's text), `Repository`, `Module`, `File` (names in component and code change texts; found by
fulltext), `Expertise` (written into person, topic and component texts), `PipelineState`, and non-eligible persons
(mailboxes, ambiguous identities). Relationships get no vectors; their explanations are in the texts of the nodes
they connect. `MENTIONS_*` appear as a `Mentions:` line on every source text.

## 4. Text assembly

Rules: lines joined by newlines, an empty line left out; the first line always says what the node is; lists are
sorted and capped (section 4.8) so the same graph always gives the same text and hash; Markdown heading markers are
stripped from document bodies; `MENTIONS_*` read only with `extracted_by = reference-extraction-v1`; the diff's
two-character `\n` sequences become real line breaks; times are shown as `YYYY-MM-DD HH:MM`; evidence identifiers
follow `docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 8 (code changes with ` v<n>`).

### 4.1 Source nodes

Every source text ends with `Mentions: <issue keys, PR names, document identifiers>` when there are any.

| Label | Template (lines) |
| --- | --- |
| `MailMessage` | `Email from {sender_name or address} to {recipient names}, {sent_at}` / `Subject: {subject}` / `In reply to: "{parent subject}"` / `{body}` |
| `SlackMessage` | `Slack message in #{channel_name} by {author_name}, {sent_at}, version {n} of {count}` / `Reply to: "{first 200 chars of the thread root, latest version}"` / `{body}` |
| `TeamsTranscriptSegment` | `Meeting transcript: {meeting title}, {started_at}, speaker {speaker_name}` / `Previous line ({speaker}): "{first 200 chars of segment n-1}"` / `{body}` |
| `IssueVersion` | `Issue {issue_key} version {n} of {count}, {version_at}` / `Status: ..., priority: ..., assignee: ..., changed by: ...` / `Title:` / `Description:` / `Acceptance criteria:` |
| `IssueComment` | `Comment on issue {issue_key} by {author_name}, {created_at}` / `Reply to: "{parent body}"` / `{body}` |
| `DocumentVersion` | `Document {identifier or document_id} ({document_type}) version {n} of {count} by {author_name}, {version_at}` / `Title:` / `Change summary:` / `{body without # markers}` |
| `PullRequest` | `Pull request {repository}#{pr_number} by {author_name}, state {state}` / `Title:` / `Description:` / `Changed files: {distinct code change paths}` |
| `PullRequestReview` | `Pull request review on {repository}#{pr_number} (PR version {v}) by {author_name}, {created_at}` / `Type: {entry_type}` / `Location: {file_path}:{line} ({side})` / `Reply to: "{parent body}"` / `{body}` |
| `CodeChange` | `Code change in {repository}#{pr_number} version {v}: {file_path} ({change_type})` / `Pull request: {PR title}` / `Before:` / `After:` / `Diff:` |

### 4.2 `Topic`

```text
Topic: {name} ({topic_type})
Issues: {issue keys via ABOUT_TOPIC}
{summary}
Events: {count}, from {first occurred_at} to {last occurred_at}
Experts: {person} ({share %}, rank {rank}), ...
Bus factor {n}: {risk sentence}; top expert {name}.
```

### 4.3 `Event`

```text
Event: {name} ({event_type}), {occurred_at}
Topic: {topic name}
Actors: {persons via ACTED_IN_EVENT}
{summary}
Caused by: {event} - {explanation}          (incoming CAUSED and CROSS_TOPIC_CAUSED)
Led to: {event} - {explanation}             (outgoing)
Root cause: {root cause} - {explanation}
Affected component: {component} - {explanation}
Contributing code: {code change} v{n} ({contribution_type}) - {explanation}
Evidence: {identifiers of EVIDENCED_BY targets}
```

### 4.4 `Component`

```text
Component: {name} ({component_type}) in repository {repository}
{summary}
Files: {paths via IMPLEMENTED_IN}
Depends on: {component} ({type}) - {explanation}
Used by: {component} ({type}) - {explanation}
Affected by events: {event names}
Root causes located here: {root causes}
Experts: ...
Bus factor {n}: {risk sentence}; top expert {name}.
```

Risk sentence: 0 `no one has recorded expertise`; 1 `knowledge risk, one person holds most of the expertise`; 2 or
more `expertise is shared by {n} people`.

### 4.5 `RootCause`

```text
Root cause: {name} ({cause_type})
{summary}
Explains: {event} - {explanation}
Located in components: {components}
Evidence: {identifiers of ROOT_CAUSE_EVIDENCED_BY targets}
```

### 4.6 `Person` (eligible) and `Community`

```text
Person: {name}
Activity: authored {n} PRs, wrote {n} PR reviews, {n} Slack messages, {n} emails, {n} issue comments,
          {n} document versions, spoke in {n} meeting segments, acted in {n} events
Expertise: {subject} ({Topic|Component}): {share %}, rank {rank}
Works with: {person} (weight {w}: {work item types}), ...
Community: {community_id} with {other members}
Collaboration network: weighted degree {d}, betweenness {b} ({role sentence})
```

Role sentence: betweenness >= 0.3 `bridges otherwise separate groups`; > 0 `sometimes connects others`; 0 `not a
bridge between groups`.

```text
Community: {community_id}, {size} members (Louvain community detection on WORKS_WITH)
Members: {names}
Collaborate on: {work item types inside the community}
Shared work items: {names, max 20}
```

### 4.7 Example (live, Kvitta data, 2026-09-30)

```text
Component: Fortnox authentication (service) in repository kvitta-api
Obtains Fortnox tokens through the new token flow and refreshes tokens before expiry.
Files: app/integrations/fortnox/auth.py
Depends on: Fortnox (calls) - Fortnox authentication requests tokens using the new Fortnox token flow.
Used by: Fortnox client (calls) - On a 401 response, the Fortnox client invokes authentication token refresh before its single retry.
Affected by events: Fortnox exports began failing with 401 Unauthorized, Hotfix restored Fortnox exports and drained queued expenses, Permanent Fortnox token-refresh fix merged
Root causes located here: Automatic token refresh was missing from the hotfix, Fortnox OAuth flow change was not adopted by the client, Fortnox developer-changelog monitoring gap
Experts: Ahmed Karimi (63.6 %, rank 1), David Okafor (27.3 %, rank 2), Sofia Berg (9.1 %, rank 3)
Bus factor 1: knowledge risk, one person holds most of the expertise; top expert Ahmed Karimi.
```

One text carries what four layers know about the component: its files (Architecture), the events and root causes
around it (Knowledge, Root cause & impact), its experts (Expertise) and its bus factor (Graph algorithms).

### 4.8 Caps

When a list is longer than its cap, the most important entries are kept and the text says `... and N more`. With the
Kvitta data the person expertise list is the one that uses its cap: Ahmed Karimi has 21 expertise entries, David
Okafor 19, Nina Petrova 18 and Maria Lindgren 16, and each text lists the 10 with the highest share.

| Cap | Value | List (kept first) |
| --- | ---: | --- |
| `MAX_RECIPIENTS` | 20 | mail recipients (by name) |
| `MAX_MENTIONS` | 20 | `Mentions:` (by name) |
| `MAX_FILES` | 20 | PR changed files, component files |
| `MAX_ISSUES` | 20 | topic issues |
| `MAX_ACTORS` | 15 | event actors |
| `MAX_CAUSAL_LINKS` | 10 | each of `Caused by` / `Led to` |
| `MAX_ROOT_CAUSES` | 10 | event and component root causes |
| `MAX_COMPONENT_LINKS` | 10 | affected components, root cause components |
| `MAX_CODE_LINKS` | 10 | contributing code |
| `MAX_EVIDENCE` | 20 | `Evidence:` |
| `MAX_EXPERTS` | 10 | experts (highest share) |
| `MAX_DEPENDENCIES` | 10 | each of `Depends on` / `Used by` |
| `MAX_AFFECTING_EVENTS` | 15 | component's events (most recent) |
| `MAX_EXPLAINED_EVENTS` | 15 | root cause's events |
| `MAX_EXPERTISE_LINES` | 10 | person's expertise (highest share) |
| `MAX_WORKS_WITH` | 10 | person's partners (highest weight) |
| `MAX_COMMUNITY_PEERS` | 15 | person's community peers |
| `MAX_COMMUNITY_MEMBERS` | 30 | community members |
| `MAX_COMMUNITY_WORK_ITEMS` | 20 | community shared work items |

## 5. Chunking

A text longer than 12 000 characters is split by `split_text`: its first line is repeated at the top of every part,
followed by `(Part i of n)`; the rest is cut into 6 000-character windows overlapping by 500, each ending at the last
line break or space in its final fifth when there is one. **Part 1** is embedded on the node itself; **parts 2..n**
become `EmbeddingChunk:Searchable` nodes linked `(:EmbeddingChunk)-[:CHUNK_OF]->(source)`, with `display_name`
`<source> (part i of n)`, `chunk_of_label`, `chunk_index`, `chunk_count`, the embedding properties and
`derived`/`generated_by = embedding-v3`/`generated_at`. A node's chunks are replaced whenever it is re-embedded and
deleted when the source is gone or no longer eligible. Chunk nodes are found by `generated_by STARTS WITH
"embedding-"`.

With the Kvitta data the design document `DESIGN-RECEIPT-READER` (`doc-002`) is the long text: both of its versions
(14 257 and 15 015 characters of body) are split into three parts, so there are 4 chunk nodes:

| Chunk | Characters of text |
| --- | ---: |
| `doc-002 v1 (part 2 of 3)` | 6 107 |
| `doc-002 v1 (part 3 of 3)` | 3 381 |
| `doc-002 v2 (part 2 of 3)` | 6 108 |
| `doc-002 v2 (part 3 of 3)` | 4 346 |

Each has its own vector and is found by both indexes: a fulltext search for a phrase that exists only in part 3 of the
document (`"TAXI STOCKHOLM"`, `baseline`) returns exactly the two part-3 chunks. When the agent's search hits a chunk,
the evidence for the document version is the text of the chunk that matched (`docs/AI_AGENT_HANDOFF.md`).

## 6. What is written on each embedded node

| Property | Meaning |
| --- | --- |
| `embedding` | the vector (1536 floats), set with `db.create.setNodeVectorProperty` |
| `embedding_text` | the text embedded (part 1 when chunked); also what the fulltext index searches |
| `embedding_model` | `text-embedding-3-large` |
| `embedding_source_hash` | SHA-256 of the full text, the group and the latest flag |
| `embedding_version` | `embedding-v3` |
| `embedding_group` | the source the node belongs to (below), so several versions fold into one search hit |
| `embedding_is_latest` | `true` for the latest version of its source, or when the source has one state |
| `embedding_parts` | number of parts (1 when not chunked) |
| `embedded_at` | run timestamp |

Plus the label `Searchable`. A node that stops being eligible loses the label and all these properties.

| Label | `embedding_group` | Latest when |
| --- | --- | --- |
| `MailMessage` | `Mail <message_id>` | always |
| `SlackMessage` | `Slack <message_id>` | highest `version_number` |
| `TeamsTranscriptSegment` | `Teams <meeting_id>/<segment_id>` | always |
| `IssueVersion` | `Issue <issue_key>` | highest `version_number` of the issue |
| `IssueComment` | `Issue comment <comment_id>` | always |
| `DocumentVersion` | `Document <document_id>` | highest `version_number` |
| `PullRequest` | `Pull request <repository>#<pr_number>` | always |
| `PullRequestReview` | `Pull request review <repository>#<pr_number>/<source_id>` | always |
| `CodeChange` | `Code change <repository>#<pr_number> <file_path>` | highest PR version that changed that file |
| `Topic`, `Event`, `Component`, `RootCause`, `Person`, `Community` | `<Kind> <key>` | always |

## 7. Indexes

| Index | Type | On | Used for |
| --- | --- | --- | --- |
| `searchable_embedding` | vector, 1536, cosine | `:Searchable(embedding)` | meaning; one index across all layers, so scores are comparable |
| `searchable_text` | fulltext | `:Searchable(embedding_text)` | exact words the vector misses: keys, PR numbers, file names, names |
| `entity_lookup` | fulltext | `Person`, `Issue`, `Document`, `PullRequest`, `Topic` on `name`, `display_name`, `title` | resolving names, including labels that are not embedded |
| `issue_key_lookup` | fulltext | `Issue.issue_key` | issue keys |

All created with `IF NOT EXISTS` on every run; the twelve per-label vector indexes of `embedding-v1` are dropped if
present. All four are ONLINE.

## 8. Build behaviour

1. Check the prerequisite and the key; ensure indexes.
2. For each label, read the nodes with their context (one read query per label, `QUERIES`), assemble texts, groups
   and latest flags, split long texts. Reads only.
3. Skip a node whose hash, model, version and number of parts all match, unless `force = true` (`Re-embed all`).
4. Embed the remaining parts in batches; a batch that still fails after retries marks its nodes failed and the run
   continues.
5. As soon as all parts of a node have vectors, write the node and replace its chunks in one transaction. A failed
   node keeps its previous state.
6. Remove the layer's properties and label from nodes no longer eligible; delete orphaned chunks.
7. Set `last_embedding_at`, `last_embedding_failures` and `embedding_config` (`config_fingerprint()`: version, model,
   dimensions, every cap, chunk sizes, `QUOTE_CHARS`, `BRIDGE_BETWEENNESS`).

Because texts carry context from every layer, rebuilding any layer changes some texts, and only those nodes are
re-embedded. A change to a text builder in code must bump `EMBEDDING_VERSION`; code is not part of the fingerprint.

## 9. Staleness

Upstream stages: `import`, `references`, `knowledge`, `architecture`, `causal`, `collaboration`, `algorithms`
(`docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 4).

## 10. API

`GET /api/embeddings`: `model`, `provider`, `dimensions`, `similarity`, `version`, `models_in_use`, `counts`
(`eligible`, `embedded`, `outdated`, `missing`, `chunks`), `per_label` (`layer`, `label`, `total`, `eligible`,
`embedded`, `outdated`, `missing`), `indexes` (all four, `NOT CREATED` before the first run), `excluded_persons`, all
eight pipeline timestamps, `needs_rerun`, `stale_reasons`, `counted_from`:

- `stored` (fast) when the layer is not stale, the last run had no failures and ran with the current configuration:
  counts come from the stored properties.
- `assembled` (exact) otherwise: every text is assembled and compared with its stored hash.

`GET /api/embeddings/nodes?layer=&label=&status=&offset=&limit=` (limit default 50, max 500): one page of the texts
table (`layer`, `label`, `display_name`, `text`, `group`, `is_latest`, `parts`, `status` = `current`, `outdated`,
`missing`, `empty`; `embedded_at`), with `total`.

`GET /api/embeddings/preview?force=`: what the next run would embed (`nodes`, `parts`, `chunks`, `characters`,
`estimated_tokens`, `per_label`); never calls OpenAI.

`POST /api/embeddings/build` body `{"force": bool}`: the summary plus `run_at`, `forced`, `embedded_now`,
`chunks_now`, `skipped_unchanged`, `skipped_empty`, `removed`, `removed_chunks`, `failed`, `failures`,
`per_label_run`, `token_usage`. Errors 409, 503, 500.

## 11. UI

Tab `Embeddings`. Buttons `Build embeddings` / `Building embeddings...`, `Re-embed all` (with confirmation), `Preview
next run` / `Previewing...`. Description: `Turns what every layer knows into searchable vectors, so a question can
enter the graph at the right place. Vectors are stored as properties on existing nodes.` A model line; the preview
result line; counts under `Written to existing nodes:`, `Nodes and relationships:` (chunks) and `Indexes:`; run
metrics right after a build. Tables: `Coverage by layer (written to existing nodes)`, `Embedded texts (property:
embedding_text)` (paged 50 at a time, `EMBEDDING_PAGE_SIZE`, filterable by layer, label and status), `Indexes (vector
and fulltext)`, `Excluded persons (existing Person nodes)`, and `Failures from the last run` when there are any.

Graph panel: the `Chunks` button (filter key `Embeddings`, `CHUNK_OF` only) and the `Entry points` toggle, which rings
every embedded node (`docs/GRAPH_DATA_HANDOFF.md`, section 10).

## 12. Current state (2026-09-30, Kvitta data)

360 eligible nodes, 360 embedded, 0 outdated, 0 missing, 4 chunks (364 vectors in total); not stale;
`last_embedding_at` `2026-09-29T20:04:26.393727+00:00`, `last_embedding_failures` 0. Excluded persons: Kvitta Support
and Kvitta Alerts (both mailboxes). The build sent 364 texts, 235 047 characters, about 59 000 tokens: under one cent
with `text-embedding-3-large`.

## 13. Search on the Kvitta data

Measured on 2026-09-29 against `searchable_embedding` and `searchable_text`:

- **Meaning across sources.** The two mails that name no issue are found by meaning. The alert `mail-006` has the
  Fortnox messages in `#incidents` as its nearest neighbours (`slack-026`, `-028`, `-029`, `-030`), and the customer's
  first Fortnox mail `mail-007` has Maria's answers `mail-008` and `mail-009`, which name `KV-7`.
- **Chunks.** Phrases that exist only in the last part of `DESIGN-RECEIPT-READER` are found in the chunk nodes
  (section 5).
- **Hybrid search in the agent.** The agent merges exact lookups, vector search and fulltext search (reciprocal rank
  fusion). With it the agent answers all 27 test questions correctly (`docs/AI_AGENT_HANDOFF.md`, section 9).

How the agent uses the indexes: hybrid search for the entry points; the text of a hit is read, not only its label;
ranking questions (lowest bus factor, highest betweenness) are answered from the metric properties directly; the Cypher
`SEARCH` clause is used for the vector index.

## 14. What the data needs for this layer

- Nothing special beyond the other layers: every text is built from what they produce.
- To exercise chunking, include a few long texts: a requirement or design document body well over 12 000 characters,
  or a long meeting segment. The whole assembled text counts (headers, mentions, context lines).
- To exercise the caps, a hub: a component many events affect, a person who works with more than 10 others, a message
  that names more than 20 identifiers.
- Several versions of messages, issues, documents and code changes, to exercise the grouping by `embedding_group`.

## 15. Code map

| File | Holds |
| --- | --- |
| `backend/embedding_pass.py` | constants and caps; `QUERIES` (one read query per label); one text builder per label; `GROUPS`; `LABELS` (label, layer, builder); `split_text`, `_batches`; indexes; the stored path (`config_fingerprint`, `read_run_markers`, `_stored_per_label`, `_stored_page`) and the assembled path (`_assemble`, `_per_label`); `load_embedding_state`, `embedding_nodes_page`, `embedding_preview`, `run_embedding_pass` |
| `backend/pipeline_staleness.py` | `UPSTREAM_BY_STAGE["embeddings"]` |
| `backend/app.py` | the four routes; filter `Embeddings` -> `CHUNK_OF`; `load_neo4j_graph` skips `Searchable` when choosing a node type and sends the vector's length instead of the vector (`NODE_PROPERTIES`) |
| `frontend/src/main.tsx` | `EmbeddingPanel`; `Chunks` and `Entry points` in the graph panel |

Adding a label: a query in `QUERIES`, a text builder, a `GROUPS` entry and a `LabelConfig` row in `LABELS`; the tab,
the index and staleness pick it up.
