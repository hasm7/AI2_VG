# Causal Layer Handoff

This document describes the Causal layer: the fourth middle-panel graph-building step that extends the causal picture around the Knowledge layer's `Topic`/`Event` graph with root causes, code contributions, affected components, and cross-topic causal links.

The layer is implemented in `backend/causal_layer.py`, exposed by `backend/app.py`, and displayed in `CausalLayerPanel` inside `frontend/src/main.tsx`.

## Purpose

The Knowledge layer already creates `(:Event)-[:CAUSED]->(:Event)` links within one topic. This layer never creates, changes, or deletes those relationships. It adds four things the Knowledge layer does not model:

- **Root causes** — the underlying reason (a requirement gap, a design decision, an implementation gap, a missing test, a process problem) behind one or more events, as `RootCause` nodes.
- **Code contributions** — which `CodeChange` contributed to which `Event` (`CONTRIBUTED_TO`).
- **Affected components** — which `Component` an `Event` concerns or impacts (`AFFECTED_COMPONENT`).
- **Cross-topic causal links** — a causal link between an event in this topic and an event in a different topic (`CROSS_TOPIC_CAUSED`), where the current dataset's single topic means this is currently always empty (see below).

## Current Local State

From `/api/causal`:

| Field | Current value |
| --- | --- |
| Root causes | 3 |
| Code contributions | 5 |
| Affected components | 10 |
| Cross-topic links | 0 |
| Within-topic links (`CAUSED`, read-only) | 6 |
| `last_layer_build_at` | `2026-09-23T18:08:46.846709+00:00` |
| `last_architecture_build_at` | `2026-09-23T18:09:26.465345+00:00` |
| `last_causal_build_at` | `2026-09-23T18:09:39.162792+00:00` |
| `needs_rerun` | `false` |
| `stale_reasons` | `[]` |

0 cross-topic links is correct: the local dataset has exactly one `Topic`, so there is no second topic to link into.

Current root causes:

| Name | Type | Components |
| --- | --- | --- |
| Fixed-expiry design did not meet the administrator session policy | design_decision | Session lifecycle policy |
| Known mobile endpoint difference was not followed up | process | Mobile session refresh endpoint |
| Web-only refresh implementation left the mobile fixed-expiry path unchanged | implementation_gap | Session lifecycle policy, Web session refresh endpoint, Mobile session refresh endpoint |

(Root cause names, component names, and exact counts vary between rebuilds since both the Architecture and Causal layers are LLM-derived and do not propose identical output every run.)

## Model and Configuration

| Constant | Value |
| --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra` |
| `OPENAI_REASONING_EFFORT` | `medium` |
| `CAUSAL_VERSION` | `causal-layer-v1` |
| `CANDIDATE_WINDOW_DAYS` | 30 |
| `MAX_ROOT_CAUSES` / `MAX_CODE_CONTRIBUTIONS` / `MAX_AFFECTED_COMPONENTS` / `MAX_CROSS_TOPIC_LINKS` | 10 / 20 / 20 / 10 (per topic) |

One model call per `Topic`. If `OPENAI_API_KEY` is missing, `/api/causal/build` returns `503`.

## Graph Model

```cypher
(:Event)-[:HAS_ROOT_CAUSE {explanation, evidence}]->(:RootCause)
(:RootCause)-[:ROOT_CAUSE_IN_COMPONENT]->(:Component)
(:RootCause)-[:ROOT_CAUSE_EVIDENCED_BY]->(:SourceNode)
(:CodeChange)-[:CONTRIBUTED_TO {contribution_type, explanation, evidence}]->(:Event)
(:Event)-[:AFFECTED_COMPONENT {explanation, evidence}]->(:Component)
(:Event)-[:CROSS_TOPIC_CAUSED {explanation, evidence}]->(:Event)
```

`RootCause` is unique by `slug`. If two topics propose the same slug, one node is kept (first name/summary/cause_type, via `MERGE ... ON CREATE SET`) and relationships from both topics accumulate onto it.

`CROSS_TOPIC_CAUSED` is a distinct type from `CAUSED`; this layer never writes `CAUSED`.

## Evidence Identifiers

Source-node identifiers follow the Knowledge layer's rules, with one change: `CodeChange` is identified as `<display_name> v<version_number>` (not just `display_name`), because the same file can appear in several PR versions.

| Label | Identifier |
| --- | --- |
| `Issue` | `issue_key` |
| `IssueVersion` | `display_name` |
| `IssueComment` | `comment_id` |
| `MailMessage` | `message_id` |
| `SlackMessage` | `message_id + " v" + version_number` |
| `TeamsMeeting` | `meeting_id` |
| `TeamsTranscriptSegment` | `segment_id` |
| `Document` | `document_id` |
| `DocumentVersion` | `display_name` |
| `PullRequest` | `display_name` |
| `PullRequestReview` | `source_id` |
| `CodeChange` | `display_name + " v" + version_number` |

Derived-node references (not evidence, must be copied exactly from the bundle): `Event` as `event:<topic_slug>/<event_slug>`, `Component` as `component:<repository>/<component_slug>`.

## Bundle Assembly (per topic)

Topic basics; all events of the topic sorted by `occurred_at`; existing `CAUSED` links between those events (read-only context); all source nodes reached via `EVIDENCED_BY` from those events; all `CodeChange` nodes whose parent PR is in the topic's `DERIVED_FROM` set; all `Component` nodes in the repositories of those code changes; up to 30 candidate events from other topics within 30 days of any event in this topic, closest in time first.

## Validation

Applied per topic, then a final cross-run pass:

- Root causes: keep only `explains_event_ids` that are events of this topic and `component_ids` that are in the bundle; drop if no remaining `explains_event_ids` or evidence; keep at most 10.
- Code contributions: drop unless `code_change_id`/`event_id` are in the bundle; dedupe on `(code_change_id, event_id)`; keep at most 20.
- Affected components: drop unless `event_id`/`component_id` are in the bundle; dedupe on `(event_id, component_id)`; keep at most 20.
- Cross-topic links: require exactly one end in this topic and the other in the candidate list; drop if the cause's `occurred_at` is later than the effect's; keep at most 10 per topic, then **dedupe on `(cause_event_id, effect_event_id)` across the whole run** (first kept).

## Constraint

```cypher
CREATE CONSTRAINT root_cause_slug IF NOT EXISTS FOR (n:RootCause) REQUIRE n.slug IS UNIQUE
```

## Build/Rebuild Behavior

1. Require `last_layer_build_at` (Knowledge) and `last_architecture_build_at` to both exist (else `409 {"error": "Run Knowledge layer and Architecture layer first."}`).
2. Require `OPENAI_API_KEY`.
3. For each topic, assemble a bundle and call the model once. A failed call leaves the existing layer untouched.
4. Only after every call succeeds: delete the previous layer by `generated_by = "causal-layer-v1"`, then write root causes, code contributions, affected components, and the globally deduped cross-topic links.
5. Update `PipelineState.last_causal_build_at`.

## Pipeline State

| Property | Written by |
| --- | --- |
| `last_layer_build_at` | Knowledge layer |
| `last_architecture_build_at` | Architecture layer |
| `last_causal_build_at` | Causal layer (this layer's own timestamp) |

Staleness (`needs_rerun` and `stale_reasons`) is computed centrally by `backend/pipeline_staleness.py`, not by this module. This layer's upstream stages, per `UPSTREAM_BY_STAGE`, are `knowledge` and `architecture`. See `GRAPH_DATA_HANDOFF.md`'s "Pipeline Staleness" section for the full rule set, including how staleness propagates transitively.

## Backend API

`GET /api/causal` returns pipeline timestamps, `needs_rerun`, `stale_reasons`, `counts`, and the `root_causes`, `code_contributions`, `affected_components`, `cross_topic_links`, `within_topic_links` tables (the last read directly from the Knowledge layer's `CAUSED` relationships, read-only), plus `root_cause_links` (one row per `HAS_ROOT_CAUSE` relationship: `event_name`, `topic_name`, `root_cause_name`, `explanation`, `evidence`) and `relationships`: one row per relationship type this layer created (`generated_by = "causal-layer-v1"`) with `relationship_type`, `from_labels`, `to_labels`, `count`, sorted by type. The endpoint labels are read from the graph, not hard-coded; a type with no relationships (currently `CROSS_TOPIC_CAUSED`) does not appear.

`POST /api/causal/build` returns the same payload plus `built_at`, `deleted_relationships`, `deleted_nodes`, `calls`, `model`, `discarded_evidence`, `token_usage`.

Errors: `409` if Knowledge or Architecture has not run; `503` if `OPENAI_API_KEY` is missing; `500` otherwise.

## Frontend UI

Tab `Root cause & impact layer` inside `BuildGraphLayersPanel`, rendered by `CausalLayerPanel`, fourth of seven inner tabs. Button text `Build root cause & impact layer` / `Building root cause & impact layer...`. "Root cause & impact layer" is the display name only; code, API routes (`/api/causal`), `CAUSAL_VERSION`, `last_causal_build_at`, and the graph filter key `Causal` keep the `causal` name. Below the button and status row, a description line (`reference-description`) reads `Finds the root causes behind events, the code changes that contributed to them, and the components they affected.` The counts row is preceded by the heading `Nodes and relationships:` (`reference-description reference-counts-heading`) and lists only what this layer creates: root causes, code contributions, affected components, cross-topic links. The within-topic link count is shown separately below it under the heading `Read from Knowledge layer:`, since `CAUSED` is created by the Knowledge layer.

Tables, in order, each with a lighter `knowledge-section-kind` suffix in its heading and on each column. Long text columns use `reference-cell-wrap` (fixed 280px, wrapping).

| Table | Heading suffix | Columns |
| --- | --- | --- |
| `Root causes` | `(node)` | Name (property), Type (property), Summary (property), Explains events (via HAS_ROOT_CAUSE), Components (via ROOT_CAUSE_IN_COMPONENT), Evidence (via ROOT_CAUSE_EVIDENCED_BY) |
| `Relationships` | `(all relationship types)` | Relationship, From → To, Count |
| `Root cause links` | `(relationship: HAS_ROOT_CAUSE)` | Event (start node), Topic (via EVENT_OF_TOPIC), Root cause (end node), Explanation (property), Evidence (property) |
| `Code contributions` | `(relationship: CONTRIBUTED_TO)` | Code change (start node), File (start node property), Event (end node), Topic (via EVENT_OF_TOPIC), Contribution (property), Explanation (property), Evidence (property) |
| `Affected components` | `(relationship: AFFECTED_COMPONENT)` | Event (start node), Topic (via EVENT_OF_TOPIC), Component (end node), Explanation (property), Evidence (property) |
| `Cross-topic links` | `(relationship: CROSS_TOPIC_CAUSED)` | Cause (start node), Cause topic (via EVENT_OF_TOPIC), Effect (end node), Effect topic (via EVENT_OF_TOPIC), Explanation (property), Evidence (property) |
| `Within-topic links` | `(relationship: CAUSED, from Knowledge layer)` | Topic (via EVENT_OF_TOPIC), Cause (start node), Effect (end node), Explanation (property); two-line caption `Created by the Knowledge layer. Rebuilding this layer does not change these links.` / `Sent to the model as context when finding the root causes above.` |

## Graph Visualization Filter

The filter button is labelled `Causes` in the graph panel; the key sent to `/api/graph` is still `Causal`.

```python
"Causal": [
    "CAUSED",
    "CROSS_TOPIC_CAUSED",
    "HAS_ROOT_CAUSE",
    "ROOT_CAUSE_IN_COMPONENT",
    "ROOT_CAUSE_EVIDENCED_BY",
    "CONTRIBUTED_TO",
    "AFFECTED_COMPONENT",
]
```

`CAUSED` is intentionally included here as well as in `Knowledge`, so the full causal picture is visible under either filter. Node color added: `RootCause` (`#f43f5e`).

## Important Boundaries

- Reads and writes Neo4j only; never touches PostgreSQL.
- Never creates, modifies, or deletes `CAUSED` relationships.
- Deletes only by `generated_by = "causal-layer-v1"`.
- Depends on both the Knowledge layer (events, `CAUSED`, `EVIDENCED_BY`) and the Architecture layer (`Component`, `DEPENDS_ON` repositories) for its evidence bundles.
