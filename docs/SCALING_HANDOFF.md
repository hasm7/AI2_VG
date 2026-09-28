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

## A few times today's data (300-400 nodes)

Assessed 2026-09-27 (today's graph: 107 nodes). Three to four times today's size needs no changes; the list below
becomes relevant at thousands of nodes.

- Speed: queries stay in milliseconds, far from the 10 s query timeout.
- Cost per question: unchanged, since every agent step has a fixed cap.
- Traversal: the same node and relationship types, so the same fixed paths; only more candidates.
- Graph panel: 300-400 nodes draw without problems; `Full graph` gets denser to read.
- Embeddings: more nodes to embed the first time, still a few cents.
- LLM layer builds: bundles about three times larger, well within the models' limits; a full rebuild costs about
  three times today's.

The one thing that can start to show: "who has the most ..." questions about people. Today all five eligible person
profiles fit in the evidence (at most 8 nodes per specialist), and each profile carries its activity counts, so the
answer compares everyone. With more than 8 eligible persons the answer compares only the profiles that were fetched and
can miss someone without saying so. The explorer already counts over the whole graph for questions the planner types
as `ranking` without metrics (seen 2026-09-27 with "which file changed most"), which covers part of this.

When the data is loaded: rebuild the layers, update the test questions' expected names, run the test set, and ask a
few "who has the most ..." questions to check that the answers hold.

## Must be fixed before large data

### Agent (`backend/ai_agent/`)

| Where | Problem | Fix |
| --- | --- | --- |
| `specialists.py`, every query in `SOURCES_QUERIES`, `CAUSES_QUERIES`, `ARCHITECTURE_QUERIES`, `PEOPLE_QUERIES` | No `LIMIT`: every candidate is fetched (with its text, in `fetch_nodes`) before 8 are kept. An issue mentioned in thousands of messages fetches thousands of nodes | `LIMIT` per query, ordered so the best candidates come first |
| `specialists.py`, `RANKING_PERSONS`, `RANKING_SUBJECTS`; `followup.py`, `RANKINGS` | Return every person / topic / component; all of it goes into the answer prompt as facts | Top and bottom N only (for example 10 each) |
| `specialists.py`, `expertise_basis` in `people` (ranking questions) | One fact per expert ranked 1-2 on every topic and component (8 today, about 2 800 characters); each fact is capped at 6 activities, but the number of facts grows with the number of subjects | Only the subjects in the ranking facts' top and bottom N, for example the lowest bus factors |
| `specialists.py`, `PARTICIPATION` | Lists every participant of a meeting and every recipient of a mail | Cap the names, with "and N more" |
| `followup.py`, `RESOLVE`, `FILE_HISTORY` | `CONTAINS` over all nodes or files, no index: slow on a large graph, may hit the 10 s timeout | Resolve names through the fulltext index `entity_lookup` (or an index on `File.path`); `LIMIT` on `FILE_HISTORY` |
| `followup.py`, `CONVERSATION` | Collects a whole meeting before its `LIMIT 12` | Take segments near the given one by `sequence_number` |
| Selection in `specialists._packet` | With many relevant nodes, "closest to the entry point, then time" becomes too coarse to pick the right 8 | Done 2026-09-28: equal priority is now decided by similarity to the question (`docs/AI_AGENT_HANDOFF.md`, section 3). Measure with the test set on the large data |
| `test_questions.json` | Written for today's graph; expected names may change | Add questions for the new data; keep running the set after each change |

**When to add `LIMIT` to the specialist queries** (discussed 2026-09-27, not done). `LIMIT` only protects speed; it
is not needed today (a specialist fetches at most a few dozen candidates) and most likely not at about 1000 nodes
either (a few hundred candidates at worst, fetched in milliseconds, far from the 10 s query timeout). At that size the
problem is which 8 nodes are kept, not speed. Add it when the graph grows to several thousand nodes and a question is
measurably slow. Two rules, or `LIMIT` makes answers worse without showing it:

- **Sort each query the same way as the final selection** in `specialists._packet` (priority, then similarity to the
  question, then time), with a limit well above `max_nodes_per_specialist`. Otherwise a node that would have been among
  the 8 is cut before it is ranked. Similarity can be sorted on in Cypher with `vector.similarity.cosine`.
- **Never `LIMIT` a list that is the answer itself**, such as meeting participants or mail recipients (`PARTICIPATION`).
  Cap those with "and N more" instead, so the answer knows there are more.

Check before committing: save what every specialist fetches for every test question (read-only, no model calls),
add the limits, and compare; with today's data the result must be identical node for node. This does not show that the
limits choose well on large data; only a run on the large data does.

**Intent rules per question type** (tried 2026-09-27, measured worse, reverted; not decided). The idea: let the
planner's question types steer what a specialist fetches, for example the whole history of a version for a `timeline`
question, the conversation around a message for a `why` question, the components that use a component for an `impact`
question. It was built behind an on/off setting and measured on the test set:

- With the setting off the code fetch was identical to before, node for node (checked for every node, specialist and
  question type, without model calls).
- With it on the result was worse: 15 of 18 against 16-17 of 18 off. The `timeline` rule for `sources` added every
  version and comment of a parent at the same priority, and with only 8 places that pushed out the node the question
  was about (`doc-001 v2` in "how did REQ-AUTH-SESSION change?"). A rule that took the start and the end of the
  candidate list made it worse, since it mixed several histories in one list.
- On today's data no question went from failing to passing with the rules on. That says little: every original test
  question already passed without them, and the test set cannot show a gain (`docs/AI_AGENT_HANDOFF.md`, section 9).
  More candidates than places is not rare even today: with a single entry point, `sources` had more than 8 in 3 of
  107 cases (up to 29) and `causes` in 14 of 107 (up to 10), and a real question has up to 8 entry points whose
  candidates add up.

Since then the selection keeps the candidates most similar to the question (the `Selection in specialists._packet` row
above), which covers much of what the rules were meant to do, without rules per question type.

When the data has grown, in this order:

1. Rebuild the layers and update the test questions' expected names.
2. Add test questions where the selection decides the answer: a timeline ("from when it was created until it was
   done", which needs both the first and the last version), a why question, a "who has the most ..." question.
3. Run the test set. Only where a question fails because the right node was not kept, add one targeted rule (for
   example: always keep the latest version of each history for a timeline question, grouped per history, never across
   histories), and run the set again after each rule.

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
