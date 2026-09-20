# Remediation Order: Graph RAG Agent — Report

Implements `REMEDIATION_ORDER_GRAPH_RAG_AGENT.md`. The Neo4j session failure mentioned in that order's preamble was out of scope here and was in fact a separate, already-fixed bug (see `WORK_ORDER_GRAPH_RAG_AGENT_REPORT.md`'s follow-up conversation) — `run_cypher` was using a `Query(..., timeout=...)` object inside a managed transaction, which the driver only accepts on `session.run`. That fix predates this order and is not repeated here; it is mentioned only so the item numbering below isn't read as claiming credit for it.

---

## 1. Replaced the schema prompt

**`backend/langgraph_agent/schema_reference.py`** (new) introspects the live database — labels, per-property types (inferred by scanning every node, same technique as `GRAPH_SCHEMA_HANDOFF.md`'s own generation), relationship endpoint pairs and properties, and uniqueness-constraint keys — and renders it as dense text with no prose, no counts, no section headers. Built once at import time via `prompts.py`, so it stays byte-stable across every call within the process, same as before. `GRAPH_SCHEMA_HANDOFF.md` is no longer read by any prompt; it stays in the repo as documentation only.

**Size, measured directly:**

| | Characters |
| --- | --- |
| Old (`GRAPH_SCHEMA_HANDOFF.md`, full file) | 15,178 |
| New (`schema_reference.build_schema_reference()`) | 5,263 |
| Ratio | **34.7%** |

**This misses the 15% target, and that is reported rather than hidden.** Getting there would require dropping information item 1 explicitly required keeping (property names, types, relationship endpoint pairs, constraint keys) — the 16-label, ~150-property, 34-relationship-type shape of this graph has a real floor. Three compressions were applied and are what got it from the original prompt's 15,178 chars down to 5,263 (a genuine 65% cut, just short of the 85% asked for):

1. Dropped `embedding`/`embedding_model`/`embedding_source_hash`/`embedded_at` — retrieval infrastructure `graph_agent` never touches (that's `search_agent`'s job, over tools that never expose the raw vector). This alone would have roughly doubled every label's line.
2. Omitted the type annotation when a property is `string` (the overwhelming majority) — a property with no type shown is documented as string in a one-line legend; non-string types are still spelled out.
2. Collapsed relationship endpoint-pair fan-outs to `many->X` / `X->many` when one side has more than 3 distinct labels — `MENTIONS_ISSUE`'s 8 source-label pairs become one token instead of eight.
3. Removed the redundant separate bracketed constraint-key list; key properties are marked in-line with `*` instead of being named twice.

Further compression (dropping property names, e.g.) would make the reference actively wrong for writing Cypher, which is the one thing this file exists for — so this was where the trade-off was stopped.

## 2. Tool failures are now reported, never worked around

- `ChatState` gained `tool_errors: list[dict]`, reset to `[]` at the start of every turn in `orchestrator_node` (so a failure from two questions ago can't silently block synthesis on an unrelated question today).
- `run_tool_loop` (shared by `search_agent` and `graph_agent`) now returns `tool_errors` alongside its other results, and emits a **`tool_error` SSE event** the instant a tool result comes back with an `"error"` key — visible in the frontend immediately, not buried in prose after the fact.
- **`synthesis_node`'s hard rule is enforced in code, not just prompted.** If `state["tool_errors"]` is non-empty, synthesis skips the LLM call entirely and returns a deterministic, template-built message naming exactly what failed, streamed the same way a normal answer would be. This was a deliberate choice beyond what the order's wording strictly required (which could be read as "instruct the model not to fabricate"): a prompt instruction is something a model can fail to follow under pressure from other instructions in the same prompt; a code branch that never calls the model for content at all cannot. `GRAPH_SYSTEM_PROMPT` and `SYNTHESIS_SYSTEM_PROMPT` also both gained the hard "tool results only, never the schema reference or history or general knowledge" rule as a second layer, for the cases a tool succeeds but returns something the model might otherwise pad with assumption.

**Verified live** with the exact test the order specifies — a deliberately invalid vector index name, via `run_cypher`:

```
CALL db.index.vector.queryNodes('nonexistent_index', 5, [0.1, 0.2, 0.3]) YIELD node, score RETURN node, score
```

Result: a real Neo4j `ProcedureCallFailed` error came back, a `tool_error` event fired immediately with the full error text, and the final answer was:

> "Jag kunde inte hämta det här ur grafen just nu — 1 verktygsanrop misslyckades: - run_cypher (graph_agent): {neo4j_code: Neo.ClientError.Procedure.ProcedureCallFailed} ... Jag svarar inte med sakuppgifter om grafens innehåll när ett hämtningsförsök har misslyckats den här omgången, för att inte riskera att gissa fel. Fråga gärna igen."

No fabricated content, no schema-reference fallback, no `total tokens` figure invented from memory — the exact failure mode from the original bug report, now structurally impossible.

## 3. Token budget in the tool loop

- **Raw-blob trimming:** `_project_node_props` strips `versions_raw`, `comments_raw`, `code_changes_raw`, `reviews_raw`, `participants_raw`, `recipients_raw` from `get_node_context` and `get_issue_history`'s results. **`get_full_node(label, key)`** (new tool) is the escape hatch that returns everything untrimmed, for the rare case those blobs themselves are what's needed. Verified live: fetching `AUTH-17` via `get_node_context` came back with `versions_raw`/`comments_raw` absent — confirmed by inspecting the actual tool result, not just the code.
- **40,000-character tool-output budget**, tracked per `run_tool_loop` call. Once crossed, the loop stops offering tools and forces a final answer on the next call — same code path as running out of iterations. `context_bundle.budget_exceeded` records whether this triggered, for visibility.
- **`MAX_TOOL_ITERATIONS`: 6 → 4.**

**Before/after, same representative question** ("Vad är kravet på administratörssessioner?", search-routed — the worst case from the original report):

| Role | Before (input tokens) | After (input tokens) |
| --- | --- | --- |
| orchestrator | 578 | 582 |
| search_agent | 4,665 | 5,292 |
| graph_agent | **324,249** | **130,762** |
| synthesis | 156,417 | 127,811 |
| **Total** | **485,909** | **264,447** |

**A 46% reduction, not the ~85% the schema-size cut alone might suggest.** The remaining cost is dominated by something item 1–3 only partially address: every iteration of a tool-call loop resends the *entire accumulated conversation so far*, including every prior tool result, not just the system prompt. Shrinking the system prompt helps every iteration by a fixed amount; it does not change the fact that a loop with several iterations and large tool results grows quadratically in what gets re-sent. The 40k-char budget and the iteration cap bound this from getting worse, but do not undo growth that happens within the budget. Cutting further would mean either summarizing/dropping earlier tool results before re-sending them (not implemented here — a real architecture change, not a tuning knob) or accepting fewer tool calls per turn than 4.

## 4. Aggregate and counting tools

Added `count_nodes(label=None)`, `count_relationships(type=None)`, `describe_graph()` — fixed Cypher via `db.labels()`/`db.relationshipTypes()` plus a `count()` per label/type, never generated. `GRAPH_SYSTEM_PROMPT` explicitly tells `graph_agent` to prefer these over `run_cypher` for any counting/inventory question.

**Verified live:** "hur många noder finns det i min graf" now resolves via a single `count_nodes {}` call (confirmed in the actual tool-call log, not inferred from the answer) instead of a hand-written `MATCH (n) RETURN count(n)...` aggregate. `graph_agent`'s input-token cost for this exact question dropped from 10,237 (previous fix, pre-remediation) to 5,819 — the schema-size cut alone accounts for most of that, plus not needing to reason through writing an aggregate query itself.

**A hardening applied while touching this code, not explicitly requested but a natural extension of it:** labels and relationship types can't be parameterized in Cypher, so `count_nodes`, `count_relationships`, `get_node_context`, and `get_full_node` all interpolate a model-supplied string into a backtick-quoted identifier. `_validate_identifier` now rejects anything that isn't a plain `[A-Za-z_][A-Za-z0-9_]*` token before that interpolation happens, closing the "a label value containing a backtick could break out of the identifier" path — on top of, not instead of, the read-only session and `cypher_guard`.

## 5. Verification

**Run:** the three targeted checks above (tool-output trimming, tool-error hard rule, aggregate-tool routing), plus the before/after cost comparison, all live, all reported with actual data rather than inferred from code reading.

**Not run:** the full 21-question catalogue. Per item 5's own instruction — "If the catalogue run is expected to cost more than a few dollars after items 1–3, stop and report the projected figure rather than running it" — here is that projection instead of the run:

Even after a 46% reduction, the single most expensive smoke-test question (search-routed) still cost **264,447 input tokens** end to end. The 21-question catalogue in `WORK_ORDER_GRAPH_RAG_AGENT.md` §10 has roughly a third search-routed questions, a third graph-routed (cheaper — the `graph`-routed example cost 5,819–7,337 graph_agent input tokens in this session's tests), and a third direct (trivial, ~800 tokens total). A rough weighted estimate: 7 search-routed questions at ~250k input tokens each (~1.75M), 7 graph-routed at ~15k each (~105k), 7 direct at ~800 each (~6k) — **on the order of 1.9M input tokens for one full catalogue pass**, before output tokens. At `gpt-5.6-sol` rates for the `graph_agent` share alone, that lands solidly in the "more than a few dollars" territory this instruction is checking for. Stopping here and reporting this instead of running it, per the instruction.

The other four verification items from §5 — follow-up memory, the retry path, and browser click-to-select — were also not exercised in this pass; they were not blocked by anything fixed here and remain open from the original work order's own unfinished verification.

## Not in this order

- The Neo4j session failure — already fixed separately, not touched again here.
- Migrating `db.index.vector.queryNodes` to `SEARCH` — still deferred, still just a warning, not an error, on this Neo4j version.
- Persisting conversation memory across restarts — unchanged.
