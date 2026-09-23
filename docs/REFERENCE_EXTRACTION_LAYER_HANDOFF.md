# Reference Extraction Layer Handoff

This document describes the deterministic reference extraction layer: the middle UI panel that creates new `MENTIONS_*` relationships in Neo4j.

The layer is implemented in `backend/reference_extraction.py`, exposed by `backend/app.py`, and displayed in the React `ReferenceExtractionPanel` inside `frontend/src/main.tsx`.

## Purpose

The imported graph preserves each source tree: mail, Slack, Teams, issues, docs, and PRs. Without extraction, the sources mostly connect through shared `Person` nodes. The reference extraction layer adds content links when source text explicitly names an existing graph entity.

Example:

- A Slack message body contains `AUTH-17`.
- The graph already has `(:Issue {issue_key: "AUTH-17"})`.
- The extraction pass creates:

```cypher
(:SlackMessage)-[:MENTIONS_ISSUE]->(:Issue)
```

This layer is deterministic. It uses regex patterns and graph lookups only. It does not call an LLM, does not interpret meaning, and does not create nodes except the shared `PipelineState` singleton.

## Current Local State

From `/api/references`:

| Field | Current value |
| --- | --- |
| `total` | 55 extracted relationships |
| `MENTIONS_ISSUE` | 22 |
| `MENTIONS_PULL_REQUEST` | 21 |
| `MENTIONS_DOCUMENT` | 12 |
| `last_extraction_at` | `2026-09-23T18:08:36.712761+00:00` |
| `last_import_at` | `2026-09-20T13:18:12.864348+00:00` |
| `needs_rerun` | `false` |
| `stale_reasons` | `[]` |

This snapshot is from immediately after a full pipeline rebuild (see the follow-up staleness work order), so `needs_rerun` is currently `false`. When source data is imported after the last extraction run, `needs_rerun` becomes `true` and `stale_reasons` explains why (see the Pipeline State section below).

## Neo4j Relationship Model

The layer creates exactly three relationship types:

```cypher
(:SourceNode)-[:MENTIONS_ISSUE]->(:Issue)
(:SourceNode)-[:MENTIONS_PULL_REQUEST]->(:PullRequest)
(:SourceNode)-[:MENTIONS_DOCUMENT]->(:Document)
```

Targets are always parent entity nodes:

- issue references point to `Issue`, not `IssueVersion`
- PR references point to `PullRequest`, not `PullRequestReview` or `CodeChange`
- document references point to `Document`, not `DocumentVersion`

Every extracted relationship has these properties:

| Property | Value |
| --- | --- |
| `derived` | `true` |
| `extracted_by` | `reference-extraction-v1` |
| `matched_text` | Exact text matched in the source property, such as `AUTH-17`. |
| `source_property` | Property where the match was found, such as `body` or `description`. |
| `extracted_at` | ISO timestamp of the extraction run. |

`derived: true` matters because these edges are conclusions from text, not direct SQL source facts.

## What Gets Scanned

The scanner reads Neo4j nodes and their properties. It does not read PostgreSQL directly.

| Label | Properties scanned | Parent entity used for self-reference checks |
| --- | --- | --- |
| `MailMessage` | `subject`, `body` | none |
| `SlackMessage` | `body` | none |
| `TeamsTranscriptSegment` | `body` | none |
| `TeamsMeeting` | `title` | none |
| `Issue` | `title`, `description` | self |
| `IssueVersion` | `title`, `description`, `acceptance_criteria` | parent `Issue` via `HAS_ISSUE_VERSION` |
| `IssueComment` | `body` | parent `Issue` via `HAS_ISSUE_COMMENT` |
| `Document` | `title`, `body` | self |
| `DocumentVersion` | `title`, `body`, `change_summary` | parent `Document` via `HAS_DOCUMENT_VERSION` |
| `PullRequest` | `title`, `description` | self |
| `PullRequestReview` | `body` | parent `PullRequest` via `HAS_PR_REVIEW` |
| `CodeChange` | `before_summary`, `after_summary` | parent `PullRequest` via `HAS_CODE_CHANGE` |

Raw JSON properties are intentionally not scanned:

- `versions_raw`
- `comments_raw`
- `reviews_raw`
- `code_changes_raw`

Their content is already represented by first-class retrieval-unit nodes. Scanning both would double-count references.

## Lookup Tables

Lookups are built from the graph at runtime. Nothing is hardcoded.

| Reference kind | Match pattern | Lookup target |
| --- | --- | --- |
| Issue key | `\b[A-Z][A-Z0-9]*-\d+\b` | `Issue.issue_key` |
| Pull request | `\b([a-z0-9][a-z0-9._-]*)#(\d+)\b` | `PullRequest.repository`, `PullRequest.pr_number` |
| Document identifier | whole-word match on a valid leading document token | leading token of `Document.title` before `:` |

Document identifier rule:

- `REQ-AUTH-SESSION: Administrator session lifetime` yields `REQ-AUTH-SESSION`.
- The identifier must match `^[A-Z][A-Z0-9-]{3,}$`.

Document identifiers are resolved first. Their spans are then consumed so `REQ-AUTH-SESSION` cannot also be treated as an issue key. One matched string should create at most one edge.

Unresolved matches are discarded. For example, the issue-key pattern can match strings like `UTF-8`; if no `Issue.issue_key` exists for that text, no relationship is created.

## Self-Reference Rule

The layer skips references from a node to its own parent entity.

Examples:

- An `IssueComment` under `AUTH-17` that says `AUTH-17` does not get `MENTIONS_ISSUE`, because `(:Issue)-[:HAS_ISSUE_COMMENT]->(:IssueComment)` already says which issue it belongs to.
- An `IssueComment` under `AUTH-19` that says `AUTH-17` does get `MENTIONS_ISSUE`, because that is a cross-reference.
- A `DocumentVersion` of `doc-001` that says `REQ-AUTH-SESSION` does not point back to its own parent `Document`.

This prevents obvious duplicate/self edges while preserving cross-source and cross-entity references.

## Rebuild Behavior

The extraction pass is rebuildable and idempotent for its own layer.

Run flow in `run_extraction(session)`:

1. Set `extracted_at` to current UTC ISO timestamp.
2. Build issue, PR, and document lookup tables from Neo4j.
3. Load every scannable node and its text properties.
4. Collect resolvable references.
5. Delete existing relationships where `extracted_by = "reference-extraction-v1"`.
6. Write the new `MENTIONS_*` relationships with `MERGE`.
7. Update `PipelineState.last_extraction_at`.
8. Return counts, lookup sizes, scanned node count, and the result-table edges.

Deletion is by property, not by relationship type. This avoids deleting any future manually/import-created relationship that happens to use a similar type but was not created by this extractor.

## Pipeline State

The layer uses:

```cypher
(:PipelineState {id: "singleton"})
```

Relevant fields:

| Property | Written by | Purpose |
| --- | --- | --- |
| `last_import_at` | SQL import paths in `viewer/app.py` | Indicates source graph data changed. |
| `last_extraction_at` | `reference_extraction.run_extraction` | Indicates reference layer was rebuilt. |

Staleness (`needs_rerun` and `stale_reasons`) is computed centrally by `backend/pipeline_staleness.py`, not by this module. This layer's only upstream stage, per `UPSTREAM_BY_STAGE`, is `import`. See `GRAPH_DATA_HANDOFF.md`'s "Pipeline Staleness" section for the full rule set, including how staleness propagates transitively from stages further upstream.

## Backend API

### `GET /api/references`

Returns current state and all extracted edges.

Payload shape:

```json
{
  "last_import_at": "2026-09-20T13:18:12.864348+00:00",
  "last_extraction_at": "2026-09-23T18:08:36.712761+00:00",
  "needs_rerun": false,
  "stale_reasons": [],
  "edges": [],
  "total": 55,
  "counts": {
    "MENTIONS_ISSUE": 22,
    "MENTIONS_PULL_REQUEST": 21,
    "MENTIONS_DOCUMENT": 12
  }
}
```

Each edge row contains:

| Field | Meaning |
| --- | --- |
| `source_label` | Label of the source node, for example `SlackMessage`. |
| `source_display_name` | Display/name property of the source node. |
| `relationship` | One of the three `MENTIONS_*` types. |
| `matched_text` | Identifier text found in the source property. |
| `source_property` | Property that contained the match. |
| `target_display_name` | Display/name of the target entity. |
| `extracted_at` | Timestamp from the run that created the edge. |

### `POST /api/references/extract`

Runs the extraction pass and returns the same state plus run metadata:

| Field | Meaning |
| --- | --- |
| `extracted_at` | Timestamp of this run. |
| `deleted` | Number of old extractor-owned relationships removed. |
| `total` | Number of new edges collected/written. |
| `counts` | Per relationship type counts. |
| `scanned_nodes` | Number of graph nodes scanned. |
| `lookups` | Number of target entities available by kind. |
| `edges` | Result rows for display. |

Errors return `{"error": "..."}` with status `500`.

## Frontend UI

The UI lives in `frontend/src/main.tsx`.

Component hierarchy:

- `BuildGraphLayersPanel`
  - tab `Reference extraction`
  - renders `ReferenceExtractionPanel`

The middle program area has three inner tabs:

1. `Reference extraction`
2. `Knowledge layer`
3. `Embeddings`

The reference tab provides:

| UI element | Source field / behavior |
| --- | --- |
| `Extract references` button | Calls `POST /api/references/extract`. |
| `Extracting...` button state | Shown while the POST is running. |
| Last extraction timestamp | `state.last_extraction_at`, formatted by `formatTimestamp`. |
| Last import timestamp | `state.last_import_at`, formatted by `formatTimestamp`. |
| Stale warning | Shown when `state.needs_rerun` is true, with each `state.stale_reasons` entry listed on its own line underneath. |
| Success message | `Done. X edges created.` after a successful run. |
| Counts row | Shows `Issue`, `Pull request`, `Document`, and `Total`. |
| Results table | Shows all `state.edges`. |

Relationship labels in the count row:

```ts
const relationshipLabels = {
  MENTIONS_ISSUE: "Issue",
  MENTIONS_PULL_REQUEST: "Pull request",
  MENTIONS_DOCUMENT: "Document",
};
```

Results table columns:

| Column | Edge field |
| --- | --- |
| Source type (Label) | `source_label` |
| Source (node name) | `source_display_name` |
| Field (property in source) | `source_property` |
| Matched (text in source) | `matched_text` |
| Target (node name) | `target_display_name` |
| Relationship (type) | `relationship` |

If no edges exist, the panel shows:

```text
No references yet. Press the button to run the pass.
```

## Graph Visualization Filter

The graph API and frontend use the filter name `References` for this layer.

`backend/app.py` maps it to:

```python
"References": [
    "MENTIONS_ISSUE",
    "MENTIONS_PULL_REQUEST",
    "MENTIONS_DOCUMENT",
]
```

Selecting `References` in the graph source filters returns only nodes connected by these relationship types and only those relationships.

## Important Boundaries

- This layer reads and writes Neo4j only.
- It does not modify PostgreSQL.
- It does not create `Issue`, `Document`, `PullRequest`, or retrieval-unit nodes.
- It does not call OpenAI or any model.
- It does not infer semantics such as "implements", "causes", "blocks", or "depends on".
- It only says: this source node explicitly mentions this existing target entity.
- Causal and semantic interpretation belongs to the Knowledge layer.
