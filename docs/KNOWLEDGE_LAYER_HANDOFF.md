# Knowledge Layer Handoff

This document describes the Knowledge layer: the second middle-panel graph-building step that creates `Topic` and `Event` nodes and relationships that tie evidence across the whole graph into a causal story.

The layer is implemented in `backend/topic_event_extraction.py`, exposed by `backend/app.py`, and displayed in `KnowledgeLayerPanel` inside `frontend/src/main.tsx`.

## Purpose

The reference extraction layer creates deterministic `MENTIONS_*` edges only when text explicitly names an entity such as `AUTH-17`, `backend-api#42`, or `REQ-AUTH-SESSION`.

The Knowledge layer sits above that. It assembles issue-centered evidence bundles from the graph, calls an LLM to propose topics, events, actors, evidence, and causal links, then runs the result through deterministic validation before writing anything to Neo4j.

It exists because important facts can be expressed without naming an identifier. For example, a Teams transcript segment can explain why work was blocked without literally saying `AUTH-17`.

## Current Local State

From `/api/knowledge`:

| Field | Current value |
| --- | --- |
| Topics | 1 |
| Events | 10 |
| Causal links | 6 |
| `last_extraction_at` | `2026-09-23T18:08:36.712761+00:00` |
| `last_layer_build_at` | `2026-09-23T18:08:46.846709+00:00` |
| `needs_layer_rerun` | `false` |
| `stale_reasons` | `[]` |

Current topic:

| Slug | Name | Type | Event count |
| --- | --- | --- | ---: |
| `administrator-session-lifetime-policy` | Administrator session lifetime policy | defect | 10 |

Current causal-link count:

- 6 `CAUSED` relationships between `Event` nodes.

(Event/topic wording and the exact event count vary slightly between rebuilds, since the model does not propose exactly the same events every run; this snapshot is from the full pipeline rebuild performed for the staleness follow-up work order.)

## Model and Configuration

The model call is configured in `backend/topic_event_extraction.py`.

| Constant | Value |
| --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra` |
| `OPENAI_REASONING_EFFORT` | `medium` |
| `EXTRACTION_VERSION` | `topic-event-extraction-v1` |
| `REFERENCE_EXTRACTOR_NAME` | `reference-extraction-v1` |
| `MAX_EVENTS_PER_TOPIC` | 15 |
| `MAX_CAUSAL_LINKS_PER_TOPIC` | 15 |

The OpenAI Responses API is used with structured output parsing. The code expects a Pydantic `TopicEventExtractionOut` object, not free text parsed as JSON.

If `OPENAI_API_KEY` is missing, the backend returns a clear `503` error for `/api/knowledge/build`.

## Neo4j Graph Model

The layer creates two node labels:

```cypher
(:Topic)
(:Event)
```

It creates these relationship types:

```cypher
(:Issue)-[:ABOUT_TOPIC]->(:Topic)
(:Topic)-[:DERIVED_FROM]->(:SourceNode)
(:Event)-[:EVENT_OF_TOPIC]->(:Topic)
(:Event)-[:EVIDENCED_BY]->(:SourceNode)
(:Person)-[:ACTED_IN_EVENT]->(:Event)
(:Event)-[:CAUSED]->(:Event)
```

Every node and relationship created by this layer is stamped with:

| Property | Value |
| --- | --- |
| `derived` | `true` |
| `generated_by` | `topic-event-extraction-v1` |
| `generated_at` | Run timestamp |

`Topic` and `Event` nodes also store `model`.

### `Topic` Node

Unique key:

```cypher
(:Topic {slug})
```

Properties:

| Property | Meaning |
| --- | --- |
| `slug` | Stable topic slug from the model/resolution layer. |
| `name` | Human-readable topic name. |
| `topic_type` | One of `requirement`, `defect`, `incident`, `decision`, `other`. |
| `summary` | Short topic summary. |
| `display_name` | Same as `name`. |
| `derived` | Always `true`. |
| `generated_by` | `topic-event-extraction-v1`. |
| `model` | Model used for generation. |
| `generated_at` | Build timestamp. |

### `Event` Node

Unique key:

```cypher
(:Event {topic_slug, slug})
```

Properties:

| Property | Meaning |
| --- | --- |
| `topic_slug` | Owning topic slug. |
| `slug` | Event slug. |
| `name` | Human-readable event name. |
| `event_type` | One of `created`, `decided`, `blocked`, `changed`, `resolved`, `regressed`, `other`. |
| `occurred_at` | Timestamp copied from evidence. Must parse as a timestamp. |
| `summary` | Short event summary. |
| `display_name` | Same as `name`. |
| `derived` | Always `true`. |
| `generated_by` | `topic-event-extraction-v1`. |
| `model` | Model used for generation. |
| `generated_at` | Build timestamp. |

### Relationship Semantics

| Relationship | Meaning |
| --- | --- |
| `ABOUT_TOPIC` | An issue belongs to a topic. Current graph has both `AUTH-17` and `AUTH-19` pointing to the same topic. |
| `DERIVED_FROM` | Topic was built from this evidence-bundle node. This includes all bundle nodes, not only cited evidence. |
| `EVENT_OF_TOPIC` | Event belongs to a topic. |
| `EVIDENCED_BY` | Event is grounded in specific source nodes cited by the model and accepted by validation. |
| `ACTED_IN_EVENT` | A resolved `Person` acted in the event. |
| `CAUSED` | One event caused or led to another event. |

`CAUSED` relationship properties:

| Property | Meaning |
| --- | --- |
| `explanation` | Human-readable explanation returned by the model and accepted by validation. |
| `evidence` | Evidence identifiers supporting the causal link. |
| `derived`, `generated_by`, `generated_at` | Standard layer metadata. |

## Constraints

The layer ensures these constraints:

```cypher
CREATE CONSTRAINT topic_slug IF NOT EXISTS
FOR (t:Topic) REQUIRE t.slug IS UNIQUE

CREATE CONSTRAINT event_key IF NOT EXISTS
FOR (e:Event) REQUIRE (e.topic_slug, e.slug) IS UNIQUE
```

These constraints are present in the current local Neo4j database.

## Topic Candidates

One topic candidate is one `Issue`.

The code reads:

```cypher
MATCH (i:Issue) WHERE i.issue_key IS NOT NULL
RETURN i.issue_key AS key
ORDER BY key
```

Current candidates:

- `AUTH-17`
- `AUTH-19`

Each candidate produces one model call. The current built layer reports two issues under one shared topic.

## Evidence Bundle Assembly

For each issue key, `assemble_bundle(tx, issue_key)` builds an evidence bundle.

The bundle starts with:

- the `Issue`
- all `IssueVersion` nodes
- all `IssueComment` nodes

Then it expands through deterministic reference edges:

```cypher
MENTIONS_ISSUE
MENTIONS_PULL_REQUEST
MENTIONS_DOCUMENT
```

Only relationships where `extracted_by = "reference-extraction-v1"` count.

The expansion works in both directions:

- nodes the issue/version/comment mentions
- nodes that mention the issue/version/comment

Additional sibling expansion:

| If bundle includes | Then add |
| --- | --- |
| `PullRequest` | Its `PullRequestReview` children and `CodeChange` children. |
| `Document` | Its `DocumentVersion` children. |
| `TeamsTranscriptSegment` | The parent `TeamsMeeting` and every transcript segment in that meeting, ordered by `sequence_number`. |

The Teams expansion is deliberate: a meeting is one conversation, and giving the model one segment without surrounding turns can create false context.

Bundle items are sorted chronologically by `occurred_at`, with undated items last.

## Bundle Identifiers

Every item gets a stable identifier. The model must cite these exact identifiers as evidence.

Examples:

| Label | Identifier rule |
| --- | --- |
| `Issue` | `issue_key`, e.g. `AUTH-17` |
| `IssueVersion` | `display_name`, e.g. `AUTH-17 v3` |
| `IssueComment` | `comment_id`, e.g. `comment-002` |
| `MailMessage` | `message_id`, e.g. `mail-003` |
| `SlackMessage` | `message_id + " v" + version_number`, e.g. `slack-006 v2` |
| `TeamsMeeting` | `meeting_id`, e.g. `meet-001` |
| `TeamsTranscriptSegment` | `segment_id`, e.g. `seg-003` |
| `Document` | `document_id`, e.g. `doc-001` |
| `DocumentVersion` | `display_name`, e.g. `doc-001 v2` |
| `PullRequest` | `display_name`, e.g. `backend-api#42` |
| `PullRequestReview` | `source_id`, e.g. `review-004` |
| `CodeChange` | `display_name`, e.g. `backend-api#42 backend/auth/session.py` |

The backend keeps:

- `valid_identifiers`: accepted evidence IDs
- `node_id_by_identifier`: map back to Neo4j element IDs for writing relationships

## Model Output Schema

The model must return:

```text
TopicEventExtractionOut
  topic: TopicOut
  events: EventOut[]
  causal_links: CausalLinkOut[]
```

`TopicOut`:

| Field | Notes |
| --- | --- |
| `slug` | Proposed topic slug. |
| `name` | Topic name. |
| `topic_type` | `requirement`, `defect`, `incident`, `decision`, or `other`. |
| `summary` | Topic summary. |
| `existing_topic_slug` | Optional exact slug for a topic already resolved earlier in this run. |

`EventOut`:

| Field | Notes |
| --- | --- |
| `slug` | Event slug unique within the topic. |
| `name` | Event name. |
| `event_type` | `created`, `decided`, `blocked`, `changed`, `resolved`, `regressed`, or `other`. |
| `occurred_at` | Must come from evidence and parse as timestamp. |
| `summary` | Event summary. |
| `evidence` | Bundle identifiers. |
| `actor_names` | Names to resolve to `Person` nodes. |

`CausalLinkOut`:

| Field | Notes |
| --- | --- |
| `cause_event_slug` | Cause event slug. |
| `effect_event_slug` | Effect event slug. |
| `explanation` | Why the cause led to the effect. |
| `evidence` | Bundle identifiers supporting the causal claim. |

## Deterministic Resolution Layer

Nothing from the model is written directly.

Validation rules in `apply_resolution`:

1. Evidence identifiers not present in the bundle are discarded.
2. Events with no surviving evidence are dropped.
3. Events whose `occurred_at` cannot parse as a timestamp are dropped.
4. `actor_names` are resolved through `person_identity.build_registry(...).resolve_person_key(name=...)`.
5. Missing actor matches are ignored; no new `Person` nodes are created.
6. Causal links are dropped if either event was dropped.
7. Self-causation is dropped.
8. Causal links with no surviving evidence are dropped.
9. Only the first 15 events and 15 causal links per topic candidate are considered.

`existing_topic_slug` is honored only when it exactly matches a topic already resolved earlier in the same run. Otherwise the proposed slug is treated as a new topic unless it already exists in the current run.

## Rebuild Behavior

The layer is rebuildable.

Run flow:

1. Require `OPENAI_API_KEY`.
2. Build the shared person identity registry from PostgreSQL.
3. Ensure `Topic` and `Event` constraints.
4. Delete relationships where `generated_by = "topic-event-extraction-v1"`.
5. Delete nodes where `generated_by = "topic-event-extraction-v1"`.
6. Load issue keys.
7. For each issue, assemble a bundle.
8. Call the model once for that bundle.
9. Validate the model output.
10. Write `Topic`, `Event`, and relationship records.
11. Update `PipelineState.last_layer_build_at`.
12. Return the current knowledge state plus run metadata.

Deletion is by `generated_by`, not by label or relationship type. This protects imported source graph data and deterministic `MENTIONS_*` reference edges.

## Pipeline State

The layer uses:

```cypher
(:PipelineState {id: "singleton"})
```

Relevant fields:

| Property | Written by | Purpose |
| --- | --- | --- |
| `last_extraction_at` | Reference extraction layer | Indicates deterministic references changed. |
| `last_layer_build_at` | Knowledge layer | Indicates `Topic`/`Event` layer was rebuilt. |

Staleness (`needs_layer_rerun` and `stale_reasons`) is computed centrally by `backend/pipeline_staleness.py`, not by this module. This layer's only upstream stage, per `UPSTREAM_BY_STAGE`, is `references`. See `GRAPH_DATA_HANDOFF.md`'s "Pipeline Staleness" section for the full rule set, including how staleness propagates transitively from `import`.

The current local state has `needs_layer_rerun: false`.

## Backend API

### `GET /api/knowledge`

Returns current topics, events, causal links, and pipeline timestamps.

Payload shape:

```json
{
  "topics": [],
  "events": [],
  "causal_links": [],
  "last_extraction_at": "2026-09-23T18:08:36.712761+00:00",
  "last_layer_build_at": "2026-09-23T18:08:46.846709+00:00",
  "needs_layer_rerun": false,
  "stale_reasons": []
}
```

Topic rows:

| Field | Meaning |
| --- | --- |
| `slug` | Topic slug. |
| `name` | Topic name. |
| `topic_type` | Topic type. |
| `summary` | Topic summary. |
| `event_count` | Count of events linked by `EVENT_OF_TOPIC`. |

Event rows:

| Field | Meaning |
| --- | --- |
| `topic_slug` | Owning topic. |
| `slug` | Event slug. |
| `name` | Event name. |
| `event_type` | Event type. |
| `occurred_at` | Event timestamp. |
| `summary` | Event summary. |
| `evidence` | Display names of evidence nodes linked by `EVIDENCED_BY`. |
| `actors` | Person names linked by `ACTED_IN_EVENT`. |

Causal-link rows:

| Field | Meaning |
| --- | --- |
| `topic_slug` | Owning topic. |
| `cause_slug`, `cause_name` | Cause event. |
| `effect_slug`, `effect_name` | Effect event. |
| `explanation` | Causal explanation. |
| `evidence` | Evidence identifiers stored on the `CAUSED` relationship. |

### `POST /api/knowledge/build`

Runs the build and returns the current state plus run metadata:

| Field | Meaning |
| --- | --- |
| `built_at` | Timestamp of this build. |
| `deleted_relationships` | Previous generated relationships removed. |
| `deleted_nodes` | Previous generated nodes removed. |
| `calls` | Number of model calls. |
| `model` | Model name. |
| `discarded_evidence` | Evidence identifiers rejected because they were not in the bundle. |
| `overflow_events` | Events beyond `MAX_EVENTS_PER_TOPIC`. |
| `overflow_links` | Links beyond `MAX_CAUSAL_LINKS_PER_TOPIC`. |
| `token_usage` | Input/output token counts when returned by the API. |

Errors:

- Missing `OPENAI_API_KEY` returns `503`.
- Other failures return `500` with `{"error": "..."}`.

## Frontend UI

The UI lives in `frontend/src/main.tsx`.

Component hierarchy:

- `BuildGraphLayersPanel`
  - tab `Knowledge layer`
  - renders `KnowledgeLayerPanel`

The middle program area has three inner tabs:

1. `Reference extraction`
2. `Knowledge layer`
3. `Embeddings`

The Knowledge tab provides:

| UI element | Source field / behavior |
| --- | --- |
| `Build knowledge layer` button | Calls `POST /api/knowledge/build`. |
| `Building knowledge layer...` state | Shown while build is running. |
| Last reference extraction timestamp | `state.last_extraction_at`, formatted by `formatTimestamp`. |
| Last knowledge build timestamp | `state.last_layer_build_at`, formatted by `formatTimestamp`. |
| Stale warning | Shown when `state.needs_layer_rerun` is true, with each `state.stale_reasons` entry listed on its own line underneath. |
| Success message | `Done. X topics, Y events, Z causal links.` |
| Metrics row | Model, Calls, Tokens, Discarded evidence. |
| Topic cards | Shows topic name, type, and summary. |
| Events table | Shows event name/type/time/summary/actors/evidence. |
| Causal links table | Shows cause, effect, explanation, and evidence identifiers. |

If no layer exists, the panel shows:

```text
No knowledge layer yet. Press the button to build it.
```

## Graph Visualization Filter

The graph API and frontend use the filter name `Knowledge` for this layer.

`backend/app.py` maps it to:

```python
"Knowledge": [
    "ABOUT_TOPIC",
    "DERIVED_FROM",
    "EVENT_OF_TOPIC",
    "EVIDENCED_BY",
    "CAUSED",
    "ACTED_IN_EVENT",
]
```

Selecting `Knowledge` in the graph source filters returns nodes connected by these relationship types and only those relationships.

## Current Storyline in the Local Graph

The current built topic is `administrator-session-lifetime-policy`.

Its events describe:

- `AUTH-17` being opened for premature administrator expiry.
- The administrator session policy being established.
- The first fixed 60-minute requirement.
- Security rejecting the fixed-window approach.
- `AUTH-17` becoming blocked.
- Work resuming after the billing incident.
- Implementation being limited to the web endpoint.
- `AUTH-17` being delivered.
- A mobile session-policy regression being reported.
- Mobile administrators still being signed out after sixty minutes.
- `backend-api#47` proposing the mobile refresh fix.

The five `CAUSED` links connect the requirement change, blocked state, delivered web-only implementation, and later mobile regression.

## Important Boundaries

- This layer reads and writes Neo4j.
- It reads PostgreSQL only to build the person identity registry for actor resolution.
- It does not modify PostgreSQL.
- It does not delete imported source nodes or deterministic reference edges.
- It depends on the reference extraction layer for its evidence-bundle expansion.
- It creates interpretation: `Topic`, `Event`, `CAUSED`, and actor/evidence relationships are derived claims, not copied source records.
- Every derived claim must remain grounded through `DERIVED_FROM`, `EVIDENCED_BY`, and `CAUSED.evidence`.
