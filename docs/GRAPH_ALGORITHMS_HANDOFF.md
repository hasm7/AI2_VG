# Layer 6: Graph Algorithms

The sixth graph-building step. With the Python library `networkx` (not Neo4j Graph Data Science) it computes
collaboration-network metrics and communities from `WORKS_WITH`, bus factor from `Expertise`, and simple component
counts, and writes them as properties on existing `Person`, `Topic` and `Component` nodes. It creates one node type of
its own, `Community`. No model.

**Verified on 2026-09-29** against `backend/graph_algorithms.py`, `backend/app.py`, `frontend/src/main.tsx` and the
live graph.

Code: `backend/graph_algorithms.py`. UI: `GraphAlgorithmsPanel`, tab `Graph algorithms` (sixth of seven).

## 1. Why it exists

Everything it needs is already in the graph. Precomputing the metrics lets the agent and the graph panel sort and
filter on them with plain Cypher instead of running graph algorithms per question.

## 2. Collaboration graph metrics (per eligible person)

An undirected weighted graph: nodes = all eligible persons (same rule as layer 5; persons without any `WORKS_WITH` are
isolated nodes); edges = `WORKS_WITH` with `weight` and `distance = 1 / weight`.

| Property | Computation |
| --- | --- |
| `collab_weighted_degree` | `G.degree(weight="weight")`: total collaboration volume |
| `collab_betweenness` | `networkx.betweenness_centrality(G, weight="distance", normalized=True)`, rounded to 4 decimals: how often the person lies on the shortest path between two others (0 to 1) |
| `community_id` | the person's Louvain community |

**Communities:** `networkx.algorithms.community.louvain_communities(G, weight="weight", seed=42)` (fixed seed, so the
same data gives the same groups). Sorted by size descending, then by the smallest `person_key` in the community, and
named `collab-1`, `collab-2`, ... An isolated person is a community of one.

## 3. Bus factor (per `Topic` and `Component`)

From the subject's `Expertise` entries sorted by `share` descending: `bus_factor` = the smallest number of top experts
whose shares sum to at least 0.5; `expert_count` = number of entries; `top_expert` = the rank-1 person's name. A
subject without expertise gets `bus_factor = 0`, `expert_count = 0`, `top_expert = null`. Bus factor 1 means one person
holds at least half of the recorded knowledge.

## 4. Component counts

`depends_on_count` (outgoing `DEPENDS_ON`), `depended_on_by_count` (incoming `DEPENDS_ON`), `affected_event_count`
(distinct events with `AFFECTED_COMPONENT` to it).

## 5. What is written

| Label | Properties |
| --- | --- |
| `Person` (eligible) | `collab_weighted_degree`, `collab_betweenness`, `community_id`, `algorithms_generated_by`, `algorithms_generated_at` |
| `Topic` | `bus_factor`, `expert_count`, `top_expert`, `algorithms_generated_by`, `algorithms_generated_at` |
| `Component` | `bus_factor`, `expert_count`, `top_expert`, `depends_on_count`, `depended_on_by_count`, `affected_event_count`, `algorithms_generated_by`, `algorithms_generated_at` |

```cypher
(:Person)-[:MEMBER_OF_COMMUNITY]->(:Community)
```

`Community` (key `community_id`, constraint `community_key`): `size`, `display_name` (= `community_id`), `derived`,
`generated_by = graph-algorithms-v1`, `generated_at`; the membership relationships carry the same stamp.

The metric properties use `algorithms_generated_by` / `algorithms_generated_at` instead of `generated_by`, because
`Topic` and `Component` already carry `generated_by` from their own layers, and overwriting it would break those
layers' delete-by-`generated_by` rebuilds.

## 6. Run behaviour

Prerequisite: `last_collaboration_build_at` (else 409 `Run Expertise & collaboration layer first.`).

1. Compute everything in Python.
2. `REMOVE` the metric properties from **every** `Person`, `Topic` and `Component` (so no stale value survives on a
   node that no longer qualifies).
3. Delete this layer's `Community` nodes and memberships (`generated_by = graph-algorithms-v1`).
4. Write communities and memberships, then the person, topic and component metrics.
5. Set `PipelineState.last_algorithms_run_at`.

`networkx` is imported by the module; `backend/app.py` imports the module only inside the two routes, so a missing
package makes these routes answer 503 without stopping the rest of the backend.

## 7. Current state (2026-09-29)

Kvitta data. 2 communities, 8 memberships, 8 eligible persons, 10 topics, 18 components analysed; 2 nodes and 8
relationships; not stale. Weighted degree, betweenness (recomputed by enumerating all shortest paths), communities,
bus factor and component counts all matched an independent recomputation.

| Person | Weighted degree | Betweenness | Community |
| --- | ---: | ---: | --- |
| Maria Lindgren | 88 | 0.2857 | collab-2 |
| Ahmed Karimi | 99 | 0.2857 | collab-1 |
| David Okafor | 105 | 0.1429 | collab-1 |
| Nina Petrova | 86 | 0.0 | collab-1 |
| Sofia Berg | 71 | 0.0 | collab-1 |
| Lucas Holm | 66 | 0.0 | collab-1 |
| Emma Chen | 53 | 0.0 | collab-1 |
| Anders Nyberg | 16 | 0.0 | collab-2 |

Communities: `collab-1` the six developers (Ahmed, David, Emma, Lucas, Nina, Sofia); `collab-2` Maria Lindgren and
the customer Anders Nyberg. David has the most collaboration; Maria (the only strong link to the customer) and Ahmed
(the shortest path from the newcomer Emma to the others) have the highest betweenness. Because all seven team members
work directly with each other, betweenness among them comes only from differences in weight.

Bus factor 1 (one person holds at least half), 13 subjects:

| Subject | Experts | Top expert |
| --- | ---: | --- |
| Component Fortnox | 1 | Ahmed Karimi |
| Component Fortnox authentication | 3 | Ahmed Karimi |
| Component Fortnox client | 4 | Ahmed Karimi |
| Component Receipt VAT rules | 3 | Sofia Berg |
| Component Receipt image storage and retention | 3 | Sofia Berg |
| Component Approval Limits API | 1 | Emma Chen |
| Component Approval Limits Client | 3 | Emma Chen |
| Component Expense sync endpoint | 1 | Lucas Holm |
| Component Expense upload | 4 | Lucas Holm |
| Topic Emma Chen onboarding first tasks | 3 | Emma Chen |
| Topic Kvitta 1.0 release | 4 | Maria Lindgren |
| Topic Upload spinner remains active after slow-network receipt upload | 2 | Nina Petrova |
| Topic Visma export of approved expenses | 2 | Maria Lindgren |

Bus factor 2: 14 subjects, among them Component Receipt reader (Sofia), Payout export (Ahmed) and every other topic.
Bus factor 3: Component Retry delivery (Lucas, 6 experts). The two knowledge risks in the story are Fortnox (Ahmed)
and the VAT rules and image storage (Sofia); see `kvitta/demofrågor.md`, question 6.

Component counts: most affecting events Retry delivery 6, then Offline expense sync endpoint, Receipt image storage
and retention and Receipt reader 5 each; most dependencies Expense upload API (depends on 3); most depended on
Fortnox (2).

## 8. What the data needs for this layer

- Enough eligible people (10 or more) for Louvain to find several real groups, and people who connect groups, so
  betweenness varies.
- Components and topics where knowledge is concentrated (bus factor 1) and others where it is spread.
- Components that many others depend on, and components affected by many events.

## 9. API

`GET /api/algorithms`: timestamps (`last_layer_build_at`, `last_architecture_build_at`, `last_causal_build_at`,
`last_collaboration_build_at`, `last_algorithms_run_at`), `needs_rerun`, `stale_reasons`, `counts` (`communities`,
`community_memberships`, `persons` (eligible), `topics`, `components`), `persons` (eligible, by betweenness
descending), `communities`, `bus_factor` (topics and components, bus factor ascending then name), `components`.

`POST /api/algorithms/run`: the same plus `run_at`, `deleted_relationships`, `deleted_nodes`. Errors 409, 503
(networkx missing), 500.

## 10. UI

Tab `Graph algorithms`. Button `Run graph algorithms` / `Running graph algorithms...`. Description: `Computes
collaboration metrics, communities and bus factor from the existing graph. Metrics are stored as properties on
existing nodes; communities are created as new Community nodes.` Counts under `Nodes and relationships:` and
`Analyzed (existing nodes):`. Tables, each with an explanatory caption: `Communities (node, Louvain community
detection algorithm)`, `Weighted degree (graph metric, written to Person node)`, `Betweenness centrality (betweenness
centrality algorithm, written to Person node)`, `Bus factor (algorithm on expertise shares, written to Topic and
Component nodes)`, `Component metrics (simple counts, written to Component node)`.

Graph filter `Algorithms`: `MEMBER_OF_COMMUNITY`.

## 11. Boundaries

- Reads and writes Neo4j only; no model; uses `networkx` locally.
- Never writes `generated_by` on nodes it did not create; clears its metrics with `REMOVE`.
- Deletes only its own `Community` nodes and memberships.
