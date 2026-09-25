# Scaling Handoff: When the Data Grows

What to check before loading much more data. **Not decided and not scheduled**; a checklist for when it becomes
relevant. Written 2026-09-26 against the current code; recheck the references before acting.

## What does not change

The node and relationship types are fixed in code: the import (`viewer/app.py`) and every layer create fixed labels,
and dynamic relationship types are always chosen from fixed lists. The LLM layers fill a fixed schema (names and
content vary, labels and categories do not). No new SQL tables are planned, so **more data means more nodes of the
same types, never new types**. The agent's specialists therefore keep covering every embedded label.

## What already holds

- **Agent cost per question stays about the same**: 8 entry points, at most 8 nodes per specialist (1200 characters
  each), capped follow-ups and explorer, a budget per question. The answer does not read more because the graph grew.
- **Embeddings**: incremental (only changed texts are re-embedded), capped lists in every text, chunking of long
  texts, batching and retries (`docs/EMBEDDING_LAYER_HANDOFF.md`, sections 5.0, 5.7, 8).

## Must be fixed before large data

### Agent (`backend/ai_agent/`)

| Where | Problem | Fix |
| --- | --- | --- |
| `specialists.py`, every query in `SOURCES_QUERIES`, `CAUSES_QUERIES`, `ARCHITECTURE_QUERIES`, `PEOPLE_QUERIES` | No `LIMIT`: every candidate is fetched (with its text, in `fetch_nodes`) before 8 are kept. An issue mentioned in thousands of messages fetches thousands of nodes | `LIMIT` per query, ordered so the best candidates come first |
| `specialists.py`, `RANKING_PERSONS`, `RANKING_SUBJECTS`; `followup.py`, `RANKINGS` | Return every person / topic / component; all of it goes into the answer prompt as facts | Top and bottom N only (for example 10 each) |
| `specialists.py`, `PARTICIPATION` | Lists every participant of a meeting and every recipient of a mail | Cap the names, with "and N more" |
| `followup.py`, `RESOLVE`, `FILE_HISTORY` | `CONTAINS` over all nodes or files, no index: slow on a large graph, may hit the 10 s timeout | Resolve names through the fulltext index `entity_lookup` (or an index on `File.path`); `LIMIT` on `FILE_HISTORY` |
| `followup.py`, `CONVERSATION` | Collects a whole meeting before its `LIMIT 12` | Take segments near the given one by `sequence_number` |
| Selection in `specialists._packet` | With many relevant nodes, "closest to the entry point, then time" becomes too coarse to pick the right 8 | Rank by search score as well; measure with the test set |
| `test_questions.json` | Written for today's graph; expected names may change | Add questions for the new data; keep running the set after each change |

### Layer builds (LLM layers)

| Where | Problem | Fix |
| --- | --- | --- |
| `topic_event_extraction.py`, `assemble_bundle` | One model call per issue; the bundle (versions, comments, everything that mentions the issue, whole meetings) has no size cap | Cap the bundle (items and characters) before the call |
| `architecture_layer.py` | One call per repository with every PR, code change and review of that repository (each text cut to 4000 characters, but the number of items is not capped); at most 20 components per repository | Cap or split the bundle per module; raise or rethink the component cap for large repositories |
| `causal_layer.py` | One call per topic with every event, every code change of the topic's PRs and every component in those repositories | Cap the bundle |
| All three | Cost grows with the number of issues, repositories and topics, and a rebuild re-runs every call | Estimate the cost before a full rebuild (like the embedding preview) |

### Graph panel (left box)

| Where | Problem | Fix |
| --- | --- | --- |
| `backend/app.py`, `load_neo4j_graph`; `frontend/src/main.tsx`, `GraphView` | `Full graph` (and every filter) sends every matching node and relationship to the browser, which draws all of them. Thousands of nodes make the panel slow or unusable | A node limit with a warning, or show a neighbourhood around a chosen node instead of the whole graph. The panel breaks easily: change it carefully and in isolation |

## Order when it becomes relevant

1. Agent query limits and ranking caps (cheap, read-only; with limits above today's counts they should not change
   today's answers; confirm with the test set).
2. Bundle caps in the LLM layers, before the first build on the large data.
3. Graph panel limit, before loading the data into Neo4j.
4. Extend the test set and run it on the large data; compare answer quality and cost per question with today's run.
