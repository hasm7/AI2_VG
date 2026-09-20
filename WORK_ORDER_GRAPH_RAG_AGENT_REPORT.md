# Work Order: Graph RAG Agent — Implementation Report

Implements `WORK_ORDER_GRAPH_RAG_AGENT.md`. Every architectural decision in that document was followed as specified.

## Files added / changed

| File | Content |
| --- | --- |
| `backend/langgraph_agent/state.py` | `ChatState` TypedDict — the StateGraph's shared state shape. |
| `backend/langgraph_agent/prompts.py` | Byte-stable module-level prompt constants for all four roles; `GRAPH_SCHEMA_REFERENCE` is `GRAPH_SCHEMA_HANDOFF.md` read once at import time. |
| `backend/langgraph_agent/tools.py` | Ten per-label vector search tool schemas (built from `embedding_pass.LABELS`, one source of truth), `search_all`, `lookup_entity`, `lookup_issue_key`, the four fixed traversal tools, `run_cypher`. Schemas are static; executors are built per request against a live session/client. |
| `backend/langgraph_agent/nodes.py` | `orchestrator_node`, `direct_node`, `search_agent_node`, `graph_agent_node`, `synthesis_node`; the manual OpenAI Responses tool-call loop (`run_tool_loop`); citation extraction and dropped-citation detection. |
| `backend/langgraph_agent/agent.py` (rewritten) | `build_graph()` / `get_compiled_graph()`, `stream_chat()` — the streaming entry point `backend/app.py` calls. |
| `backend/cypher_guard.py` | Read-only enforcement, standalone module, no LLM dependency. |
| `backend/app.py` | `POST /api/ai/chat` rewritten as an SSE endpoint: `{"message", "thread_id"}` in, `status`/`token`/`sources`/`done` events out. `503` on missing `OPENAI_API_KEY`, checked eagerly before the stream opens so the HTTP status line is still meaningful. |
| `frontend/src/main.tsx` | `GraphView` converted to `forwardRef` with an imperative `selectNodeByDisplayName(label, displayName)` — the "minimal selection API" the work order asked for if none existed. New `ChatPanel` component: SSE consumption via `fetch` + manual `ReadableStream` parsing (not `EventSource`, since it only supports GET), message history, streaming render, status line, citation chips with click-to-select, dropped-citation warning, `thread_id` persisted in `sessionStorage`. |
| `frontend/src/styles.css` | New `.chat-*` rules; removed the now-dead `.ai-chat`/`.ai-chat-field`/`.ai-chat-output` rules the old inline chat used. |

## Architecture, as built

`START -> orchestrator`. `orchestrator` returns `Command(goto=...)` to `direct`, `search_agent`, or `graph_agent` directly — no separate conditional-edge functions were needed; LangGraph 1.2 routes on `Command.goto` from inside a node. `search_agent -> graph_agent` is a static edge. `graph_agent` also returns `Command`: normally `goto="synthesis"`, but `goto="search_agent"` once (capped via `retry_count`) if nothing citable came back from traversal on a `search`-routed question. `direct` and `synthesis` both `-> END`.

Streaming is native LangGraph, not a hand-rolled substitute: nodes call `langgraph.config.get_stream_writer()` to emit `status` events, and `direct_node`/`synthesis_node` additionally stream `token` events from inside `client.responses.stream(...)`. `backend/app.py` consumes this with `compiled.stream(input_state, config, stream_mode=["custom", "values"])` — `"custom"` chunks are exactly the events already shaped for SSE; `"values"` chunks are full state snapshots, and the last one gives the final answer/citations/token_usage for the `done` event.

Conversation memory is LangGraph's `InMemorySaver` checkpointer, keyed by `thread_id`. Confirmed empirically (not just from docs) that omitting `history` from the input on a later call is enough — the checkpointer merges input over the last checkpoint for that thread automatically, so `agent.py` only has to write the new turn back with `update_state` after each exchange; it never needs to read old history back out by hand. **Restarting the backend process loses all conversation memory** — `InMemorySaver` holds nothing on disk, exactly as the work order asked for ("note in the report what would be needed to persist across server restarts, do not build it"). Persisting across restarts would need a `langgraph.checkpoint.*` implementation backed by a store — `SqliteSaver`/`PostgresSaver` packages exist in the LangGraph ecosystem and would be the natural next step, most likely against the same Postgres this project already runs, but that is a separate package (`langgraph-checkpoint-sqlite` or `-postgres`) not currently in `requirements.txt`.

## Read-only enforcement (section 4)

`cypher_guard.enforce_read_only()` strips string literals and comments, then matches `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`, `FOREACH`, `LOAD CSV`, and `db.create.*`/`apoc.create.*`/`apoc.merge.*` on word boundaries. Verified directly (not just by reading the code):

```
MATCH (n:Issue) SET n.title = 'x' RETURN n   -> rejected: "SET"
MATCH (n:Issue) WHERE n.created_at > '2026-01-01' RETURN n  -> allowed, LIMIT 100 injected
CALL db.create.setNodeVectorProperty(...)     -> rejected (matches CREATE inside "db.create")
```

`created_at` does not trip the `CREATE` check, confirmed — this was the specific case the work order called out to test.

**A second, independent layer exists beyond what the work order asked for:** the Neo4j session every tool runs against is opened with `default_access_mode="READ"` (`agent.py`'s `stream_chat`), so even a write that somehow slipped past the regex guard would still be rejected by Neo4j itself. This costs nothing extra to have and matches the work order's own reasoning for keeping the code guard after a read-only role exists — "two layers cost nothing."

## Citations (section 7)

Citations are never taken from the model's own claims about what it cited. `nodes.collect_citable_nodes` walks every tool result recursively and builds a `key -> {label, display_name, source_url}` map from actual tool output. `extract_citations` then scans the *finished* answer text for literal occurrences of those known keys — a citation can only appear in the `sources` event if its exact key string is both in the bundle and in the rendered answer. This makes a hallucinated citation structurally impossible, not just unlikely, which is a stronger guarantee than the work order asked for ("drop any key the bundle does not contain"). `find_dropped_citations` separately regex-scans the answer for identifier-shaped substrings (`AUTH-17`, `AUTH-17 v3`, `mail-002`, `backend-api#42 review-005`, ...) that are *not* in the bundle, so a model inventing a plausible-looking but wrong identifier is still visible in `dropped_citations` rather than silently absent.

Click-to-select is wired to the real graph view, not a duplicate: `GraphView` now exposes `selectNodeByDisplayName(label, displayName)` via `useImperativeHandle`. It force-switches the graph's source filter to `"All"` (so the node is loadable regardless of which filter was active), matches on `(type, label)` against the currently loaded nodes, then reuses the exact same select/center/animate logic the graph's own click handler already used — no duplicated rendering logic.

## Bugs found and fixed during verification

Both surfaced only under a real multi-turn tool-call loop, not from static reading — this is why the work order's request for an actual smoke test mattered:

1. **`response.output` items cannot be echoed straight back into `input`.** The OpenAI Responses API on this environment's models rejects several server-populated fields with `Unknown parameter` (`status`, then on retry also `async_`) when a `response.output` item is fed back verbatim as the next call's `input`. Fixed in `nodes._sanitize_output_item`: for a `function_call` item, only `type`/`call_id`/`name`/`arguments` are kept; other item types get the same handful of known-bad keys stripped. This is now the only place output items are echoed back, so the fix covers every tool-calling node (`search_agent`, `graph_agent`) uniformly.
2. **`client.responses.create(..., stream=True)` was the wrong call for streaming.** It returns a bare `Stream` with no `get_final_response()`. The dedicated `client.responses.stream(...)` method returns a manager whose entered context (`ResponseStream`) has both the iterable events and `get_final_response()`. Fixed in `direct_node` and `synthesis_node` before any live call was attempted — caught by inspecting the installed SDK's source directly rather than guessing.

## Verification performed

Per explicit confirmation, a 3-question smoke test was run live (not the full 21-question catalogue — see the cost note below and the final decision under it):

| Question | Expected route | Actual route | Citations | Dropped |
| --- | --- | --- | --- | --- |
| "Vad är ett vektorindex?" | direct | **direct** ✅ | — | — |
| "Vem äger AUTH-19?" | graph | **graph** ✅ | Erik Nilsson, AUTH-19 v2, AUTH-19 | none |
| "Vad är kravet på administratörssessioner?" | search | **search** ✅ | doc-001 v2, AUTH-17 v5, AUTH-19 v2, doc-001, AUTH-17, AUTH-19 | none |

All three routed correctly, all citations resolved against real tool output (none hallucinated, none dropped), and answers were factually consistent with the underlying data. This confirms: routing works, both tool-calling specialists work, streaming works end-to-end over real SSE-shaped events, citation extraction works, and the Neo4j read-only session plus `cypher_guard` never blocked a legitimate read anywhere in the three runs.

**Not run:** the full 21-question catalogue (§11 item 1), the retry-after-unusable-search-results path (§11 didn't explicitly require forcing this, but it's untested live), the frontend click-to-select interaction in a browser, and a follow-up-question memory check. The code paths for all of these were written to the specification and are unit-testable in isolation (`cypher_guard`'s two required cases were verified directly; `collect_citable_nodes`/`extract_citations`/`find_dropped_citations` are pure functions), but were not exercised as a full live pass in this session.

## One cost finding worth acting on before this goes further

`graph_agent` sends the entire `GRAPH_SCHEMA_HANDOFF.md` as its system prompt on **every** iteration of its tool-call loop, and the loop's `input` grows every iteration (each tool result gets appended). On the search-routed smoke question, `graph_agent` alone used **324,249 input tokens** for one question. This is architecturally exactly what the work order specified (full live schema in the prompt, `gpt-5.6-sol` for graph work) and prompt caching should discount the repeated schema prefix at 10%, but the *absolute* size is large enough that it deserves a decision before running the full 21-question catalogue or going to production: either accept the cost, trim `GRAPH_SCHEMA_HANDOFF.md` to a denser reference for prompting purposes (the current file is written for human/LLM handoff *reading*, not for repeated inclusion in a tool loop), or cap `MAX_TOOL_ITERATIONS` more aggressively than the current 6. Not fixed in this pass — flagged instead, since changing it changes the graph_agent's effective capability and that trade-off is a product decision, not an implementation detail.

## Deprecation note carried over from the embeddings work order

`db.index.vector.queryNodes`, used by every one of the ten `search_<label>` tools, still emits the Neo4j `2026.08.1` deprecation warning pointing at a `SEARCH` clause (same finding as `WORK_ORDER_EMBEDDINGS_REPORT.md`). It works correctly today; migrating to `SEARCH` syntax is worth doing before this becomes a compatibility problem on a future Neo4j upgrade, but was out of scope here.
