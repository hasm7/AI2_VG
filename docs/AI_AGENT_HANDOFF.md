# AI Agent Handoff

This document describes the graph question-answering agent behind `Chat with AI` and its view in `Configure AI agent`.
It is implemented in `backend/ai_agent/`, exposed as `POST /api/ai/chat` and `GET /api/ai/agent` in `backend/app.py`,
and used by `ChatPanel` and `ConfigureAgentPanel` in `frontend/src/main.tsx`.

**Status (2026-09-25):** steps 1-3 of the plan are done (section 9): the full flow runs, the specialists have their
bounded model follow-up, the explorer runs read-only Cypher, and `Configure AI agent` draws the flow, the state and the
last run. All models are `gpt-4o` to test the flow cheaply. The previous agent in `backend/langgraph_agent/` is kept,
unused, for comparison until the new one replaces it.

## 1. Design Principles

- **Let code fetch, let the model think.** Retrieval (lookup, hybrid search, traversal through the layers) is
  ordinary code. A model is called to understand the question (planner), for one bounded follow-up per specialist,
  as a fallback explorer, and to write the answer.
- **Plan once, no supervisor loop.** The planner decides once which specialists run. There is no model call between
  steps to decide the next step, so the cost per question is predictable.
- **Specialists follow the graph's layers**, because each layer has its own relationships and its own questions.
- **Every model step is capped**: rounds, tool calls, extra nodes, rows, characters; a budget per question stops the
  optional steps. No prompt carries the full history or a large schema.
- **Every node and edge is declared**, so the compiled graph can be drawn completely in `Configure AI agent`.

## 2. Pattern

Plan-and-execute with parallel fan-out (LangGraph's orchestrator-worker pattern), with a corrective step:

```
START -> prepare -> planner -+-> answer                                   (small talk)
                             +-> entry -+-> sources      -+
                                        +-> causes       -+-> check -+-> answer -> END
                                        +-> architecture -+          +-> explorer -> answer
                                        +-> people       -+
```

`entry` starts the chosen specialists with `Send`; they run in parallel and each appends its packet to `evidence`.
`check` runs once after all of them.

## 3. Nodes

| Node | Kind | Does | Reads | Writes |
| --- | --- | --- | --- | --- |
| `prepare` | code | Loads `settings.json`, computes layer staleness (`pipeline_staleness`), trims history to `history_turns` | `question`, `recent_history` | `settings`, `staleness`, `recent_history` |
| `planner` | model, 1 call, structured output (`PlanOut`) | Question types, language, entities, English keywords, specialists | `question`, `recent_history`, `settings` | `plan`, `usage` |
| `entry` | code + 1 embedding call | Lookup (issue keys, PR refs, document ids exactly; names via `entity_lookup`), vector search (`searchable_embedding`, Cypher `SEARCH`), fulltext (`searchable_text`, English keywords); merged by reciprocal rank fusion, best hit per `embedding_group`. Then the fan-out | `question`, `plan`, `settings`, `usage` | `entry_points`, `usage`, `errors` |
| `sources` | code + model follow-up | Source records around the entry points: the entry nodes, versions/comments/reviews/code changes of an entry issue, document or PR, evidence of entry events, nodes that mention an entry issue/PR/document | `question`, `plan`, `entry_points`, `settings`, `usage` | `evidence`, `usage`, `errors` |
| `causes` | code + model follow-up | Events, root causes and topics: entry events, events evidenced by entry sources, root causes of entry events, events a code change contributed to, root causes in an entry component, events of an entry issue's topic | same | same |
| `architecture` | code + model follow-up | Components: entry components, components affected by entry events, holding entry root causes, evidenced by entry sources, implemented in files an entry code change or PR modifies, dependency neighbours | same | same |
| `people` | code + model follow-up | Eligible persons and communities: entry persons, actors of entry events, authors of entry sources, experts on entry subjects, community members, meeting participants, mail recipients; who took part in an entry meeting or mail as a citable item on that meeting or mail; for `ranking` questions the metric tables as facts | same | same |
| `check` | code | Enough evidence? No entry points, no evidence, a `why` question without causal evidence, or a `ranking` question without metrics means no. Its routing also reads `explorer_ran`, `usage`, `settings` | `plan`, `entry_points`, `evidence`, `explorer_ran`, `usage`, `settings` | `sufficiency` |
| `explorer` | model + read-only Cypher | Runs at most once, only when `check` says no, the explorer is on and the budget allows (section 4) | `question`, `evidence`, `sufficiency`, `settings` | `evidence`, `usage`, `explorer_ran` |
| `answer` | model, 1 streamed call | Answers from the evidence only, cites node references in square brackets, mentions stale layers, answers in the planner's `language`. Refuses in code (no model call) when a fetch failed | `question`, `recent_history`, `plan`, `staleness`, `evidence`, `errors`, `settings` | `final_answer`, `citations`, `dropped_citations`, `usage` |

Every node also appends one `trace` entry (time and a summary) through the `traced` wrapper in `nodes.py`.

A specialist ranks candidates by priority (0 = the entry point itself, 1 = one step away, 2 = further, 3 = added by
its follow-up), keeps `max_nodes_per_specialist`, and returns their `embedding_text` (cut to `max_text_chars`), which
already carries each node's context from every layer. Nodes without an embedded text (`Issue`, `Document`) get a short
fallback text.

## 4. Model Follow-up and Explorer (`followup.py`)

**Specialist follow-up.** After the code fetch, one model call (`models.specialists`) sees the question and a compact
view of the packet (label, name and a 160-character snippet per node, not the full texts). It either replies DONE or
calls up to `max_calls_per_round` of its own tools; there are `max_rounds` rounds (1 by default, so the results are not
sent back to the model). At most `max_extra_nodes` new nodes are added. It is skipped when the budget is already spent.
A failed follow-up only means no extra evidence; it is not an error.

| Specialist | Tools (fixed, parameterised Cypher; name arguments are resolved case-insensitively) |
| --- | --- |
| `sources` | `get_timeline(reference)`: versions, comments, reviews, code changes and mentioning messages of an issue, document or PR, in time order. `get_conversation(reference)`: a message's Slack thread, mail reply chain, or a segment's whole meeting. `search_sources(query)`: fulltext restricted to source labels |
| `causes` | `get_causal_chain(event)`: events up to three causal steps away. `get_root_causes(subject)`: root causes of an event, in a component, or behind a topic. `search_causes(query)` |
| `architecture` | `get_component(component)`: the component, its dependency neighbours and the code changes to its files. `get_file_history(path)`. `search_architecture(query)` |
| `people` | `get_person(name)`: the profile and the people they work with. `get_experts(subject)`: expertise shares and ranks on a topic or component (facts). `rank(metric)`: betweenness, weighted degree or bus factor (facts) |

**Explorer.** A model (`models.explorer`) with one tool, `run_cypher`. Every query goes through
`cypher_guard.enforce_read_only` (rejects writes, adds a LIMIT of `max_rows`) and runs in a read transaction with the
query timeout. At most `max_queries` queries; each result is cut to `max_rows` rows and `max_result_chars` characters
before the model sees it; errors are shown to the model so it can correct the query. Element ids in the rows become
evidence nodes (at most `max_extra_nodes`), the rows become facts. Its prompt holds a compact, fixed schema (labels,
key properties, every relationship type written as `-[:TYPE]->`) and asks for case-insensitive text matching.
The executed queries are listed in the trace.

## 5. State

`backend/ai_agent/state.py`, one question per run. Conversation history is kept outside the graph (`graph.py`,
per `thread_id`, last 20 messages, at most 200 threads), so every question starts from a clean state.

| Field | Content | Merge |
| --- | --- | --- |
| `question`, `recent_history` | The question; the last `history_turns` turns | overwrite |
| `settings`, `staleness` | Settings for this question; `{stage: {stale, reasons}}` | overwrite |
| `plan` | `question_types`, `language`, `entities`, `keywords_en`, `specialists`, `route`, `reason` | overwrite |
| `entry_points` | `id`, `label`, `name`, `score`, `via` (lookup, vector, fulltext) | overwrite |
| `evidence` | One packet per specialist and the explorer: `specialist`, `nodes` (`id`, `label`, `name`, `at`, `priority`, `text`), `facts`, `candidates`, `errors`, `followup` (log), `ms` | **appended** |
| `sufficiency`, `explorer_ran` | `{ok, reason}`; whether the explorer ran | overwrite |
| `final_answer`, `citations`, `dropped_citations` | The answer; resolved and unresolved references | overwrite |
| `usage` | Per model call: `node`, `model`, `input_tokens`, `cached_tokens`, `output_tokens`, `cost_usd`, `priced` | **appended** |
| `trace` | Per node run: `node`, `ms`, `summary` | **appended** |
| `errors` | Per failed fetch: `node`, `tool`, `error` | **appended** |

A node may not share its name with a state field in LangGraph, hence `final_answer` rather than `answer`. The `Send`
payload to a specialist carries the `usage` so far, so each parallel specialist can check the budget.

## 6. Settings

`backend/ai_agent/settings.json`, read at the start of every question (an edit applies to the next question, no
restart). Missing keys fall back to `DEFAULTS` in `settings.py`.

| Key | Current | Meaning |
| --- | --- | --- |
| `models.planner`, `.specialists`, `.explorer`, `.answer` | `gpt-4o` | Model per model step |
| `history_turns` | 3 | Earlier turns the planner and the answer see |
| `budget_usd_per_question` | 0.05 | Above this, optional steps (follow-ups, explorer) are skipped; the answer always runs |
| `specialists.<name>` | all `true` | Turn a specialist off |
| `specialist_followup` | enabled, 1 round, 2 calls per round, 4 extra nodes | Follow-up caps |
| `explorer` | enabled, 3 queries, 25 rows, 4000 characters, 6 extra nodes | Explorer caps |
| `search.vector_k`, `fulltext_k`, `lookup_k`, `entry_points` | 10, 10, 3, 8 | Hits per search and entry points kept |
| `evidence.max_nodes_per_specialist`, `max_text_chars` | 8, 1200 | Size of each evidence packet from the code fetch |
| `query_timeout_seconds` | 10 | Timeout on every Neo4j query |
| `prices_usd_per_million_tokens` | `gpt-4o`, `gpt-4o-mini`, `text-embedding-3-large` | Used for `cost_usd`. **Check against OpenAI's current price list**; a model missing here is counted as 0 with `priced: false` |

The query embedding always uses the embedding layer's model and dimensions (`EMBEDDING_MODEL`,
`EMBEDDING_DIMENSIONS` from `embedding_pass.py`); it is not a setting, since vector search needs the same model.

## 7. Neo4j Sessions and Connections

One driver per question (`stream_chat`), closed by its `with` block when the question ends, also when the stream is
interrupted. Every session is opened in a `with` block (`prepare`, `entry`, each specialist in `run_specialist`, the
explorer) and closed when the block ends, also on an error. Each parallel specialist has its own session, since a
session is not thread-safe; its follow-up runs in the same session. All sessions are read sessions and every query
runs in a read transaction with a timeout.

Checked on 2026-09-25 by watching the server (`dbms.listConnections`, `SHOW TRANSACTIONS`) every 50 ms during a
question with two parallel specialists: 0 backend connections before, at most 2 during (one per specialist), 0
connections and 0 running transactions one second after.

## 8. API and Frontend

`POST /api/ai/chat` with `{"message", "thread_id"}`, streamed as Server-Sent Events: `status`, `token`, `tool_error`,
`sources`, `done`. `done` also carries `usage`, `cost_usd`, `plan`, `entry_points`, `sufficiency` and `trace`.

`GET /api/ai/agent` (read-only, `describe.py`): `nodes` (`id`, `title`, `kind`, `model`, `enabled`, `description`,
`reads`, `writes`), `edges` (`source`, `target`, `conditional`, `label`), `state` (`name`, `type`, `merge`,
`description`, `written_by`, `read_by`) and `settings`. Nodes and edges come from `get_compiled_graph().get_graph()`,
so the drawing matches what runs; state fields and merge rules come from `AgentState`; descriptions, reads and writes
come from `NODE_INFO` in `describe.py` (keep it in step with `nodes.py`).

`Configure AI agent` tab (`ConfigureAgentPanel`), in order:

| Part | Content |
| --- | --- |
| Agent flow | SVG drawn top-down: a node's row is its longest path from the start, so the parallel specialists share a row. Blue = code, violet = model call, green = code + bounded model follow-up, dark pills = start/end; a node turned off in the settings is faded. Dashed edges are conditional, with their label (the four Send edges share one). Edges that skip rows run in their own lane along the side. After a question in the chat, the nodes and edges it went through are amber, with time and cost under each node. Clicking a node selects it |
| Node details | Title, kind, model, description, the state fields it reads and writes, and its last-run summary |
| State | Every `AgentState` field: type, merge rule, written by, read by, meaning; rows used by the selected node are highlighted |
| Last run | Question, plan, check result, total cost, and per node: time, model, tokens in, cached, out, cost, summary |
| Settings | The current `settings.json`, read-only |

The last run is kept in `App` (`lastAgentRun`), set by `ChatPanel` through `onRunComplete` when a `done` event arrives.

## 9. Verification (2026-09-25)

| Check | Result |
| --- | --- |
| Compiled graph | 10 nodes plus start/end; every edge present, including the conditional ones |
| Follow-up tools | All 12 tools run against the live graph and return nodes or facts |
| `Varför blockerades AUTH-17?` | `sources` and `causes` in parallel; follow-ups `get_timeline(AUTH-17)`, `get_conversation(slack-007)`, `get_root_causes(...)`; correct answer with 3 citations; $0.016 |
| `Vilka komponenter påverkades av mobilregressionen och varför?` | `architecture` and `causes`; follow-ups `get_component` x2, `get_root_causes`; both components and both root causes cited; $0.016 |
| `Vilka var med på sprint review-mötet?` | First answered from the wrong evidence (fixed: `people` now adds meeting participants and mail recipients); now Anna Berg, Anna Lindqvist, Erik Nilsson, cited `[meet-002]`; $0.010 |
| Explorer, called directly: "Who took part in the sprint review meeting?" | One query, 3 rows, the three persons as evidence; $0.007 |
| Neo4j connections | Section 7 |
| `GET /api/ai/agent` through the Vite proxy | 12 nodes, 17 edges, 15 state fields |
| `tsc --noEmit`, `py_compile` | Pass |

Known weak points: the follow-up model sometimes passes a Swedish word from the question as a tool argument (returns
nothing, costs little); answers in Swedish are somewhat stiff with `gpt-4o`. The tab has not been checked for layout
at every window width.

## 10. Next Steps

1. ~~Skeleton with a cheap model~~.
2. ~~Draw nodes, edges and state in `Configure AI agent`~~.
3. ~~Specialist follow-ups and the explorer~~.
4. Test questions with expected answers (section 12 of `EMBEDDING_LAYER_HANDOFF.md` plus the project goals), with
   answer quality and cost per question.
5. Edit settings from the tab (model per node, budget, specialists on/off), choose models per step, fine-tuning. Then
   remove `langgraph_agent/`.

## 11. Code Map

| File | Holds |
| --- | --- |
| `backend/ai_agent/graph.py` | `build_graph` (nodes, edges, declared conditional targets), `stream_chat`, history per thread, `MissingApiKeyError`, `require_openai_client` |
| `backend/ai_agent/nodes.py` | `traced`, `prepare`, `planner` (`PlanOut`), `entry`, `dispatch_specialists` (fan-out), the four specialist nodes, `check`, `explorer`, `answer` (evidence text, citation checking) |
| `backend/ai_agent/retrieval.py` | Reference detection, lookups, vector and fulltext search, reciprocal rank fusion, group folding |
| `backend/ai_agent/specialists.py` | The four specialists' code fetches, participation items, rankings, `run_specialist` (own session per specialist) |
| `backend/ai_agent/followup.py` | Follow-up tools per specialist, `run_followup`, `run_explorer` |
| `backend/ai_agent/describe.py` | `NODE_INFO`, `EDGE_LABELS`, `STATE_DESCRIPTIONS`, `describe_agent` for `GET /api/ai/agent` |
| `backend/ai_agent/db.py` | `read` (read transaction with timeout), `fetch_nodes`, `node_text` |
| `backend/ai_agent/state.py` | `AgentState` |
| `backend/ai_agent/prompts.py` | `PLANNER_PROMPT`, `ANSWER_PROMPT`, `followup_prompt`, `EXPLORER_PROMPT` |
| `backend/ai_agent/settings.py`, `settings.json` | Settings and defaults |
| `backend/ai_agent/usage.py` | Usage entries, cost, totals |
| `frontend/src/main.tsx` | Agent types, `layoutAgentGraph`, `routeAgentEdges`, `traversedAgentEdges`, `ConfigureAgentPanel`; `ChatPanel` `onRunComplete`; `App` `lastAgentRun` |
| `frontend/src/styles.css` | `agent-panel` (many-table group), `agent-flow-*`, `agent-node-details`, `agent-state-row-used` |

## 12. Important Boundaries

- Reads Neo4j only (read sessions, read transactions); never writes to Neo4j or PostgreSQL.
- Model-written Cypher goes through `cypher_guard` and a read transaction: two independent guards.
- Source texts are data, never instructions (stated in every prompt).
- When a fetch fails, the answer states no facts (decided in code).
