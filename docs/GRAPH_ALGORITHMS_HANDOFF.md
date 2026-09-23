# Graph Algorithms Handoff

This document describes the Graph algorithm layer: the sixth middle-panel graph-building step, which precomputes collaboration-network metrics and bus-factor metrics using `networkx` and writes them as plain properties on existing nodes.

The layer is implemented in `backend/graph_algorithms.py`, exposed by `backend/app.py`, and displayed in `GraphAlgorithmsPanel` inside `frontend/src/main.tsx`.

## Purpose

Everything this layer needs already exists in the graph (`WORKS_WITH`, `Expertise`, `DEPENDS_ON`, `AFFECTED_COMPONENT`). Rather than adding new derived facts, it precomputes graph metrics — weighted degree, betweenness centrality, community membership, bus factor — so the AI chat agent and the graph view can filter and sort on them with ordinary Cypher, without running `networkx` at query time.

Uses the Python library `networkx`. Does not use Neo4j Graph Data Science.

## Current Local State

From `/api/algorithms`:

| Field | Current value |
| --- | --- |
| Communities | 2 |
| Persons | 7 |
| Topics | 1 |
| Components | 3 |
| `last_architecture_build_at` | `2026-09-23T17:22:40.434771+00:00` |
| `last_causal_build_at` | `2026-09-23T17:25:27.631397+00:00` |
| `last_collaboration_build_at` | `2026-09-23T17:26:03.752708+00:00` |
| `last_algorithms_run_at` | `2026-09-23T17:26:07.152902+00:00` |
| `needs_rerun` | `false` |

The dataset has only 5 eligible persons, so these results exist but are not statistically meaningful — this is expected (see the work order, section 7).

Communities:

| Community | Size | Members |
| --- | ---: | --- |
| `collab-1` | 3 | Anna Lindqvist, Erik Nilsson, Priya Raman |
| `collab-2` | 2 | Anna Berg, Martin Ek |

Bus factor:

| Subject | Bus factor | Experts | Top expert |
| --- | ---: | ---: | --- |
| Mobile session refresh endpoint (Component) | 1 | 2 | Erik Nilsson |
| Administrator session lifetime policy compliance (Topic) | 2 | 4 | Anna Lindqvist |
| Session validity policy (Component) | 2 | 4 | Priya Raman |
| Web session refresh endpoint (Component) | 2 | 3 | Anna Lindqvist |

Excluded persons (`Support`, `Anna`) never receive `collab_*`/`community_id` properties — their values stay `null`, which is expected since they have no `WORKS_WITH` edges to begin with.

## Properties Written to Existing Nodes

Before writing, all properties in the relevant row are `REMOVE`d from every node of that label, so stale values from a previous run never remain — even for a node that no longer qualifies (e.g. a component that lost all its dependencies).

| Label | Properties |
| --- | --- |
| `Person` | `collab_weighted_degree`, `collab_betweenness`, `community_id`, `algorithms_generated_by`, `algorithms_generated_at` |
| `Topic` | `bus_factor`, `expert_count`, `top_expert`, `algorithms_generated_by`, `algorithms_generated_at` |
| `Component` | `bus_factor`, `expert_count`, `top_expert`, `depends_on_count`, `depended_on_by_count`, `affected_event_count`, `algorithms_generated_by`, `algorithms_generated_at` |

`algorithms_generated_by` / `algorithms_generated_at` are used instead of the normal `generated_by` / `generated_at`, specifically so this layer never overwrites the stamp that `Topic` and `Component` nodes already carry from the Knowledge and Architecture layers — overwriting `generated_by` on those nodes would break their own layer's delete-by-`generated_by` rebuild logic.

## Collaboration Graph Metrics

Undirected weighted graph: nodes are all eligible persons (the same eligibility rule as the Collaboration layer), including persons with no `WORKS_WITH` edge (they appear as isolated nodes / singleton communities). Edges are every `WORKS_WITH`, with `weight` and `distance = 1 / weight`.

- `collab_weighted_degree` = `G.degree(weight="weight")`.
- `collab_betweenness` = `networkx.betweenness_centrality(G, weight="distance", normalized=True)`, rounded to 4 decimals.
- Communities = `networkx.algorithms.community.louvain_communities(G, weight="weight", seed=42)`.

Community IDs are assigned `collab-1`, `collab-2`, ... after sorting communities by size descending, then by the smallest `person_key` in the community ascending.

## Bus Factor

For each `Topic` and `Component`: read its `Expertise` nodes (via `EXPERTISE_IN`), sorted by `share` descending. `bus_factor` = the smallest number of top experts whose combined `share` is at least `0.5`. `expert_count` = number of `Expertise` nodes. `top_expert` = the rank-1 person's name. A subject with no expertise gets `bus_factor = 0`, `expert_count = 0`, `top_expert = null`.

## Component Metrics

`depends_on_count` (outgoing `DEPENDS_ON`), `depended_on_by_count` (incoming `DEPENDS_ON`), `affected_event_count` (distinct `Event` with `AFFECTED_COMPONENT` to it).

## Graph Model

```cypher
(:Person)-[:MEMBER_OF_COMMUNITY]->(:Community)
```

`Community` is the only new node label, keyed by `community_id`, properties `size` and `display_name` (= `community_id`). Unlike the metric properties above, `Community` nodes and `MEMBER_OF_COMMUNITY` edges use the **normal** stamping (`derived`, `generated_by = "graph-algorithms-v1"`, `generated_at`) and are deleted by `generated_by` on rerun, same as any other layer's own nodes.

## Constraint

```cypher
CREATE CONSTRAINT community_key IF NOT EXISTS FOR (n:Community) REQUIRE n.community_id IS UNIQUE
```

## Build/Rebuild Behavior

1. Require `last_collaboration_build_at` to exist (else `409 {"error": "Run Collaboration layer first."}`).
2. No model call. Compute all metrics into Python data structures first.
3. `REMOVE` the algorithm properties from `Person`, `Topic`, `Component`; delete the previous `Community`/`MEMBER_OF_COMMUNITY` layer by `generated_by = "graph-algorithms-v1"`.
4. Write the new `Community` nodes and `MEMBER_OF_COMMUNITY` edges, then the `Person`/`Topic`/`Component` metric properties.
5. Update `PipelineState.last_algorithms_run_at`.

## Pipeline State

| Property | Written by |
| --- | --- |
| `last_architecture_build_at` | Architecture layer |
| `last_causal_build_at` | Causal layer |
| `last_collaboration_build_at` | Collaboration layer |
| `last_algorithms_run_at` | Graph algorithm layer (this layer's own timestamp) |

`needs_rerun` is `true` when `last_algorithms_run_at` is missing, or when any of the three upstream timestamps is newer than it. Note this table intentionally does **not** include `last_layer_build_at` (Knowledge): a Knowledge-layer rebuild only makes this layer stale indirectly, once the Causal or Collaboration layers that actually depend on it are rebuilt and produce a newer timestamp.

## Backend API

`GET /api/algorithms` returns pipeline timestamps, `needs_rerun`, `counts`, and the `persons` (sorted by `collab_betweenness` descending), `communities`, `bus_factor` (sorted by `bus_factor` ascending then name), `components` tables.

`POST /api/algorithms/run` returns the same payload plus `run_at`, `deleted_relationships`, `deleted_nodes`.

Errors: `409` if Collaboration has not run; `500` otherwise. Requires the `networkx` package (added to `requirements.txt`); if it is not installed, both endpoints return `503` rather than breaking the rest of the backend, because the import is deferred to request time.

## Frontend UI

Tab `Graph algorithms` inside `BuildGraphLayersPanel`, rendered by `GraphAlgorithmsPanel`, sixth of seven inner tabs. Button text `Run graph algorithms` / `Running graph algorithms...`. Tables, in order: `Persons`, `Communities`, `Bus factor`, `Components`.

## Graph Visualization Filter

```python
"Algorithms": [
    "MEMBER_OF_COMMUNITY",
]
```

Node color added: `Community` (`#a3e635`).

## Important Boundaries

- Reads and writes Neo4j only; never touches PostgreSQL.
- Calls no model; uses `networkx` locally.
- Never overwrites `generated_by` on `Topic`/`Component`/`Person` nodes it did not create — uses `algorithms_generated_by` instead.
- `Community` nodes are the only ones this layer deletes by `generated_by` on rebuild; the metric properties are cleared with `REMOVE` instead, since they live on nodes owned by other layers.
- Depends on the Architecture, Causal, and Collaboration layers all having run at least once.
