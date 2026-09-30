# Scaling Handoff: When the Data Grows

A plan for loading much more data than the Kvitta example: what already scales, and which adjustments to make, in
which order, when the graph grows to thousands of nodes. Every code reference was checked against the code on
2026-09-30. For the size of an example dataset, see `docs/DATA_GENERATION_GUIDE.md`, section 2.

## What stays the same

The node and relationship types are fixed in code: the import (`viewer/app.py`) and every layer create fixed labels,
and dynamic relationship types are always chosen from fixed lists. The LLM layers fill a fixed schema (names and
content vary, labels and categories do not). **More data means more nodes of the same types, never new types**, so
the agent's specialists keep covering every embedded label.

## What already scales

- **Agent cost per question stays about the same**: 8 entry points, at most 8 nodes per specialist (1200 characters
  each), capped follow-ups and explorer, a budget per question. Complete lists (the sources of a topic, the people
  outside the team, the groups, a causal chain) are given as one fact line each instead of more nodes.
- **Embeddings**: incremental (only changed texts are re-embedded), capped lists in every text, chunking of long
  texts, batching and retries (`docs/EMBEDDING_LAYER_HANDOFF.md`, sections 4.8, 5, 8).
- **Graph panel**: the graph API sends no embedding vectors, the force layout runs in steps with a progress line, and
  the Knowledge view keeps its proportions (`docs/GRAPH_DATA_HANDOFF.md`, section 10).

## The Kvitta size (about 550 nodes)

The Kvitta data has 542 nodes and 2 851 relationships after all layers. At this size:

- queries run in milliseconds, far from the 10 s query timeout;
- the agent answers all 27 test questions, about $0.015 per question;
- the full graph loads in about 3 seconds and is laid out in about 12 seconds in the browser;
- a full embedding build costs under one cent.

## Adjustments for thousands of nodes

### Agent (`backend/ai_agent/`)

| Where | Today | Adjustment |
| --- | --- | --- |
| `specialists.py`, every query in `SOURCES_QUERIES`, `CAUSES_QUERIES`, `ARCHITECTURE_QUERIES`, `PEOPLE_QUERIES` | Every candidate is fetched (with its text) before the best 8 are kept | `LIMIT` per query, sorted like the final selection (below) |
| `specialists.py`, `RANKING_PERSONS`, `RANKING_SUBJECTS`; `followup.py`, `RANKINGS` | Every person, topic and component as facts | Top and bottom N only (for example 10 each) |
| `specialists.py`, `expertise_basis` in `people` (ranking questions) | One fact per expert ranked 1-2 on every topic and component | Only the subjects in the ranking facts' top and bottom N |
| `specialists.py`, `person_roster` in `people` (general who-questions) | Every person with counts and mail subjects, at most 30 persons | Names and e-mail domains only past a size, with the total |
| `specialists.py`, `topic_source_facts` | Every source of a topic by name | Counts per kind past a size, with the most similar sources by name |
| `specialists.py`, `PARTICIPATION` | Every participant of a meeting and every recipient of a mail | Cap the names, with "and N more" |
| `followup.py`, `RESOLVE`, `FILE_HISTORY` | `CONTAINS` over all nodes or files | Resolve names through the fulltext index `entity_lookup` (or an index on `File.path`); `LIMIT` on `FILE_HISTORY` |
| `followup.py`, `CONVERSATION` | Collects a whole meeting before its `LIMIT 12` | Take segments near the given one by `sequence_number` |
| `test_questions.json` | Written for the Kvitta graph | Add questions for the new data; keep a set of control questions |

**Adding `LIMIT` to the specialist queries.** A specialist fetches at most a few dozen candidates on the Kvitta data,
in milliseconds; `LIMIT` becomes useful at several thousand nodes. Two rules keep the answers the same:

- **Sort each query the same way as the final selection** in `specialists._packet` (priority, then similarity to the
  question, then time), with a limit well above `max_nodes_per_specialist`, so no node that would have been among the
  8 is cut before it is ranked. Similarity can be sorted on in Cypher with `vector.similarity.cosine`.
- **Never `LIMIT` a list that is the answer itself**, such as meeting participants or mail recipients
  (`PARTICIPATION`); cap those with "and N more" so the answer knows there are more.

Before committing: save what every specialist fetches for every test question (read-only, no model calls), add the
limits, and compare; on the Kvitta data the result must be identical node for node.

**Choosing among many candidates.** The selection keeps the candidates most similar to the question within each
priority, and complete lists travel as facts. With much more data: add test questions where the selection decides the
answer (a timeline from the first to the last version, a why question, a "who has the most ..." question), run the set,
and where the right node was not kept, add one targeted rule and run the set again.

### Layer builds (LLM layers)

| Where | Today | Adjustment |
| --- | --- | --- |
| `topic_event_extraction.py`, `assemble_bundle` | One model call per issue with its whole bundle | Cap the bundle (items and characters) before the call |
| `architecture_layer.py` | One call per repository with every PR, code change and review (each text at most 4000 characters); at most 20 components per repository | Split the bundle per module; raise the component cap for large repositories |
| `causal_layer.py` | One call per topic with every event, code change and component in reach | Cap the bundle |
| All three | Cost grows with the number of issues, repositories and topics | Estimate the cost before a full rebuild (like the embedding preview) |

### Graph panel (left box)

| Where | Today | Adjustment |
| --- | --- | --- |
| `backend/app.py`, `load_neo4j_graph`; `frontend/src/main.tsx`, `GraphView` | Every filter sends and draws every matching node and relationship | A node limit with a notice, or a neighbourhood around a chosen node. Change the panel carefully and in isolation |

## Order

1. Agent query limits and ranking caps (cheap, read-only; confirm with the test set that the Kvitta answers are
   unchanged).
2. Bundle caps in the LLM layers, before the first build on the large data.
3. Graph panel limit, before loading the data into Neo4j.
4. Extend the test set and run it on the large data; compare answer quality and cost per question.
