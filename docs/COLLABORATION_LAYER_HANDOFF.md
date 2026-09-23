# Collaboration Layer Handoff

This document describes the Collaboration layer: the fifth middle-panel graph-building step that computes who has expertise in what, and who actually works with whom, from activity already present in the graph.

The layer is implemented in `backend/collaboration_layer.py`, exposed by `backend/app.py`, and displayed in `CollaborationLayerPanel` inside `frontend/src/main.tsx`.

## Purpose

Unlike the Knowledge, Architecture, and Causal layers, this layer is **fully deterministic** — it calls no model. It answers two questions from activity counts alone:

- **Expertise**: for each person and each subject (a `Topic` or a `Component`), how much weighted activity does that person have on that subject, grounded in the specific activity nodes counted (`EXPERTISE_EVIDENCED_BY`).
- **Collaboration**: which pairs of people share work items (issues, PRs, meetings, mail threads, events), and how often.

## Current Local State

From `/api/collaboration`:

| Field | Current value |
| --- | --- |
| Expertise entries | 14 |
| Persons with expertise | 4 |
| Collaboration pairs | 7 |
| Excluded persons | 2 |
| `last_import_at` | `2026-09-20T13:18:12.864348+00:00` |
| `last_layer_build_at` | `2026-09-23T18:08:46.846709+00:00` |
| `last_architecture_build_at` | `2026-09-23T18:09:26.465345+00:00` |
| `last_causal_build_at` | `2026-09-23T18:09:39.162792+00:00` |
| `last_collaboration_build_at` | `2026-09-23T18:10:20.780808+00:00` |
| `needs_rerun` | `false` |
| `stale_reasons` | `[]` |

Excluded persons (matches the local dataset exactly as expected):

| Person | Reason |
| --- | --- |
| Support | mailbox |
| Anna (unresolved name-only observation) | ambiguous identity |

Top expertise by topic (`Administrator session lifetime policy`): Anna Berg (score 25, share 0.275, rank 1), Anna Lindqvist (25, 0.275, rank 2), Erik Nilsson (24, 0.264, rank 3), Priya Raman (17, 0.187, rank 4).

Strongest collaboration pair: Anna Lindqvist <-> Erik Nilsson, weight 8 (shared across `AUTH-19`, two PRs, two meetings, one mail thread, two events).

(Exact scores, ranks, and topic naming vary slightly between rebuilds because the Knowledge layer is LLM-derived; this snapshot is from the full pipeline rebuild performed for the staleness follow-up work order.)

## Weights

| Relationship from `Person` | Target label | Weight |
| --- | --- | ---: |
| `AUTHORED_PR` | `PullRequest` | 3 |
| `WROTE_PR_REVIEW` | `PullRequestReview` | 2 |
| `AUTHORED_DOCUMENT_VERSION` | `DocumentVersion` | 2 |
| `ACTED_IN_EVENT` | `Event` | 2 |
| `CHANGED_ISSUE_VERSION` | `IssueVersion` | 1 |
| `WROTE_ISSUE_COMMENT` | `IssueComment` | 1 |
| `SENT_SLACK_MESSAGE` | `SlackMessage` | 1 |
| `SENT_MAIL` | `MailMessage` | 1 |
| `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` | `TeamsTranscriptSegment` | 1 |

`MIN_EXPERTISE_SCORE = 2`: expertise below this score is not written at all.

## Eligible Persons

All `Person` nodes except `actor_type = "mailbox"` or `identity_ambiguous = true`. Excluded persons are returned by the API with their reason.

## Subjects

A subject is a `Topic` or a `Component`. An activity node belongs to:

- a `Topic` T if `(T)-[:DERIVED_FROM]->(activity)` exists, or the activity is an `Event` with `(activity)-[:EVENT_OF_TOPIC]->(T)`.
- a `Component` C if it is a `PullRequest` (or `PullRequestReview` of one) whose `CodeChange` `MODIFIES_FILE` a `File` that C is `IMPLEMENTED_IN`; or `(C)-[:COMPONENT_EVIDENCED_BY]->(activity)`; or it is an `Event` with `(activity)-[:AFFECTED_COMPONENT]->(C)`.

An activity node counted once per subject even if it matches multiple rules for that subject.

## Graph Model

```cypher
(:Person)-[:HAS_EXPERTISE]->(:Expertise)
(:Expertise)-[:EXPERTISE_IN]->(:Topic)
(:Expertise)-[:EXPERTISE_IN]->(:Component)
(:Expertise)-[:EXPERTISE_EVIDENCED_BY]->(:ActivityNode)
(:Person)-[:WORKS_WITH {weight, shared_work_items, work_item_types}]->(:Person)
```

`Expertise` key: `(person_key, subject_label, subject_key)`. `subject_key` is `Topic.slug`, or `<source_instance>/<repository>/<component_slug>` for a component. Properties: `score`, `share` (rounded to 3 decimals), `rank` (1 = highest score, ties broken by `person_key` ascending), `activity_count`, `first_activity_at`, `last_activity_at`, `display_name` = `<person name> – <subject name>`.

`WORKS_WITH` is written once per pair, from the person with the lexicographically smaller `person_key` to the other; the relationship is undirected in meaning. `weight` = number of distinct shared work items (at most 50 listed in `shared_work_items`).

Work items and who is connected to them:

| Work item | Persons connected |
| --- | --- |
| `Issue` | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE` to the issue, and `CHANGED_ISSUE_VERSION` to any of its `IssueVersion` nodes |
| `PullRequest` | `AUTHORED_PR`, `REVIEWED_PR` |
| `TeamsMeeting` | `PARTICIPATED_IN_MEETING` |
| `MailMessage` | `SENT_MAIL` (sender) and `MAIL_RECIPIENT` (recipients) |
| `Event` | `ACTED_IN_EVENT` |

## Constraint

```cypher
CREATE CONSTRAINT expertise_key IF NOT EXISTS FOR (n:Expertise) REQUIRE (n.person_key, n.subject_label, n.subject_key) IS UNIQUE
```

## Build/Rebuild Behavior

1. Require `last_layer_build_at`, `last_architecture_build_at`, and `last_causal_build_at` to all exist (else `409 {"error": "Run Knowledge, Architecture and Causal layers first."}`).
2. No model call; nothing can fail partway that would require the delete-after-success ordering the LLM layers use. The layer still deletes only its own previous data (`generated_by = "collaboration-layer-v1"`) before writing.
3. Compute activities, subjects, expertise, and collaboration pairs; write `Expertise`, `HAS_EXPERTISE`, `EXPERTISE_IN`, `EXPERTISE_EVIDENCED_BY`, `WORKS_WITH`.
4. Update `PipelineState.last_collaboration_build_at`.

## Pipeline State

| Property | Written by |
| --- | --- |
| `last_import_at` | SQL import |
| `last_layer_build_at` | Knowledge layer |
| `last_architecture_build_at` | Architecture layer |
| `last_causal_build_at` | Causal layer |
| `last_collaboration_build_at` | Collaboration layer (this layer's own timestamp) |

Staleness (`needs_rerun` and `stale_reasons`) is computed centrally by `backend/pipeline_staleness.py`, not by this module. This layer's upstream stages, per `UPSTREAM_BY_STAGE`, are `import`, `knowledge`, `architecture`, and `causal`. See `GRAPH_DATA_HANDOFF.md`'s "Pipeline Staleness" section for the full rule set, including how staleness propagates transitively.

## Backend API

`GET /api/collaboration` returns pipeline timestamps, `needs_rerun`, `stale_reasons`, `counts`, and the `expertise`, `works_with`, `excluded_persons` tables. `expertise` is sorted by subject name then rank; `works_with` by weight descending.

`POST /api/collaboration/build` returns the same payload plus `built_at`, `deleted_relationships`, `deleted_nodes`.

Errors: `409` if an upstream layer has not run; `500` otherwise. This layer never returns `503`, since it calls no model.

## Frontend UI

Tab `Collaboration layer` inside `BuildGraphLayersPanel`, rendered by `CollaborationLayerPanel`, fifth of seven inner tabs. Button text `Build collaboration layer` / `Building collaboration layer...`. Tables, in order: `Expertise`, `Collaboration`, `Excluded persons`.

## Graph Visualization Filter

```python
"Collaboration": [
    "HAS_EXPERTISE",
    "EXPERTISE_IN",
    "EXPERTISE_EVIDENCED_BY",
    "WORKS_WITH",
]
```

Node color added: `Expertise` (`#22d3ee`).

## Important Boundaries

- Reads and writes Neo4j only; never touches PostgreSQL.
- Calls no model; deterministic and cheap to rebuild.
- Deletes only by `generated_by = "collaboration-layer-v1"`.
- Depends on the Knowledge layer (`Topic`/`Event`), the Architecture layer (`Component`), and the Causal layer (`AFFECTED_COMPONENT`) for subject membership.
- Never creates `Person` nodes; only reads and links existing ones.
