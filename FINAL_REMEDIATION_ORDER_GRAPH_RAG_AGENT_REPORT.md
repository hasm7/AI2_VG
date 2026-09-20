# Final Remediation Order: Graph RAG Agent — Report

Implements items 1–4 of `FINAL_REMEDIATION_ORDER_GRAPH_RAG_AGENT.md`. **Stopping here, per item 3's own instruction**: `graph_agent` measured at 198,389 input tokens on the representative question — above the 60,000 threshold that instruction sets for stopping and reporting instead of proceeding to item 5. This report is that diagnosis.

---

## 1. Context bundle contract — implemented, and it worked exactly as intended for `synthesis`

`graph_agent_node` now ends its tool loop, then makes one more `client.responses.parse` call on the same (cached) transcript, requesting a `ContextBundleOut` (Pydantic: `nodes: list[BundleNodeOut]`, each `label`/`key`/`display_name`/`text`/`relations`/`source_url`). Capped at 15 in the prompt and again defensively in code (`raw_nodes[:BUNDLE_NODE_LIMIT]`). `text` is truncated to 1,500 characters in code regardless of what the model returns. `synthesis` now receives `{"question", "nodes", "tool_errors", "budget_exceeded"}` — never the tool transcript, never `graph_agent`'s intermediate reasoning. `collect_citable_nodes` is no longer used for `graph_agent`'s output; citation metadata is now built directly from the bundle's own `nodes`, exactly as instructed ("the bundle *is* the citable set").

**Result on `synthesis`, measured:** **1,468 input tokens** on the representative question, versus 127,811 before this order and versus a 10,000-token target. That's a 98.8% reduction — the bundle contract did exactly its job for the node it targeted.

## 2. Search-result projection — implemented as a single shared funnel, and a finding along the way

**The premise in the order didn't match this codebase's actual state.** The ten `search_<label>` tools were already returning a hand-built 6-field projection (`label`, `key`, `display_name`, `score`, `text_preview`, `source_url`) — they never passed `properties(node)` through to the model in the first place, so `versions_raw`/`comments_raw`/etc. were never reaching the context via that path. This is worth flagging because the order's diagnosis ("a vector search returning five Issue nodes carries versions_raw...") described a bug that wasn't present in the search tools specifically — it was present in `get_node_context` and `get_issue_history`, which is exactly where the projection had already been applied in the previous pass.

**Built anyway, because the architectural instruction is correct regardless of the premise:** `tools.strip_raw_blob_properties` is now a recursive stripper applied at **one point** — inside `nodes.run_tool_loop`, immediately after every executor call, for every tool except `get_full_node` — rather than scattered per-tool calls. This is strictly better than the previous per-tool approach: a new tool added later is covered automatically, nothing has to remember to call a projection function. The two previous call sites (`get_node_context`, `get_issue_history`) were removed since the central funnel now covers them.

**Whether the 40,000-character budget binds, reported as asked:** yes, it now fires reliably — "Verktygsbudget nådd" appeared in 2 of the 4 live test questions in this pass, including the representative one. It did not fire in the earlier report specifically because nothing had ever measured *whether* it fired; the mechanism itself was correct before and after this change. **What the budget does not do, and was never going to do, is bound `graph_agent`'s total token cost** — see the diagnosis below.

## 3. Cost re-measurement — the stop condition triggered

Same question as every previous measurement: "Vad är kravet på administratörssessioner?"

| Role | Original | After item 1–4 of the previous order | **After this order** |
| --- | --- | --- | --- |
| orchestrator | 578 | 582 | 582 |
| search_agent | 4,665 | 5,292 | 7,571 |
| graph_agent | 324,249 | 130,762 | **198,389** |
| synthesis | 156,417 | 127,811 | **1,468** |
| **Total** | 485,909 | 264,447 | 207,010 |

Total cost dropped again (207,010 vs. 264,447, a further 22% cut), driven entirely by `synthesis`. **`graph_agent` went up**, not down (130,762 → 198,389), and is well above the 60,000-token stop threshold this item sets. Per the item's own instruction, stopping here instead of proceeding to item 5.

### Diagnosis: why `graph_agent` got more expensive, not less

Two things are true at once:

1. **The char-budget mechanism works correctly and did exactly what it was built to do** — bound *new* tool output added during the loop to 40,000 characters, then force a tool-free final call. It fired on this exact question.
2. **That was never going to bound `graph_agent`'s total token cost, because the budget was never the dominant cost.** Every iteration of `client.responses.create(..., input=input_items, tools=...)` resends the *entire* accumulated `input_items` list — every prior tool call, every prior tool result, the growing conversation — not just new content. With `MAX_TOOL_ITERATIONS = 4`, a loop that reaches 4 iterations resends a conversation that's grown 4 times, and the *last* iteration's single call is priced on the full accumulated size, not the incremental size the budget tracks. The bundle-assembly call added by item 1 makes this worse in one specific way: it is a 5th call on top of however large the transcript already grew inside the loop — item 1 shrinks what leaves `graph_agent` (huge win for `synthesis`), but adds one more expensive call *before* that shrinking happens, priced on the same swollen transcript.

The retry path (item 4.2, below) makes this mechanism's cost visible in the extreme: a question that triggers one retry runs the entire `graph_agent` loop-plus-bundle-call sequence **twice**, back to back, each on a transcript that itself hit the budget cap. That question measured **432,645** `graph_agent` input tokens — worse than the pre-remediation baseline on the original report's worst question.

**What this means for a further pass, not attempted here since diagnosis was the instructed deliverable at this stop point:** the token-count problem is now structural, not a matter of prompt size. The fix has to change what gets *resent* on each iteration, not what gets added — e.g., summarizing or dropping the bodies of earlier tool results before the next iteration's call (keeping only their keys/identifiers, re-fetchable by `get_full_node` if truly needed again), capping `MAX_TOOL_ITERATIONS` more aggressively specifically for the loop-before-bundle phase, or restructuring so the bundle-assembly call is not an *additional* call on the full transcript but replaces the final loop iteration instead of following it.

## 4. Functional verification

**4.1 Follow-up memory: pass, with an accuracy finding alongside it.** "Vem äger AUTH-19?" → "Erik Nilsson" (correct). Follow-up on the same `thread_id`, "Och vem granskade pull requesten som löste den?" — the checkpointer correctly carried history forward (confirmed directly: the orchestrator's own input token count grew from 578 to 1,210 between the two calls, meaning the prior turn was actually present, not merely assumed). The answer treated "den" as referring to the AUTH-19 topic rather than asking "which issue?" — the memory mechanism itself worked. **The answer was still "I couldn't find a pull request for this"**, which is a retrieval miss, not a memory failure — a PR of that description exists in the graph, but `graph_agent`'s tool loop for that specific follow-up (capped at 4 iterations, same budget constraints) didn't surface it in the nodes it was given. Reported as a finding, not fixed here.

**4.2 Retry path: pass.** A question about entities not present in the graph ("Zorblax-kommittén", "Marswidget-projektet") produced exactly the designed sequence: `search_agent` → `graph_agent` (0 usable bundle nodes) → **one** return to `search_agent` (`retry_count` went to 1, confirmed in state) → `graph_agent` again → `synthesis`, which correctly reported that nothing was found rather than fabricating a committee decision. The cap-at-one behavior is correct. The cost consequence of this path is the finding under item 3 above.

**4.3 Click-to-select in a browser: not run.** Requires a running frontend and manual interaction; nothing in items 1–3 touched `GraphView` or `ChatPanel`, so there's no new risk to it, but it also wasn't re-verified in this pass.

---

## Standing constraints — confirmed unchanged

- Read-only guard (`cypher_guard`), the read-only Neo4j session, and `_validate_identifier` untouched.
- The tool-failure hard rule is still enforced in `synthesis_node`'s code path, not only in prompts — unaffected by this order's changes, and re-confirmed working by the fact that neither test question in this pass hit a tool error and neither produced a fabricated answer.
- `GRAPH_SYSTEM_PROMPT`, `SEARCH_SYSTEM_PROMPT`, etc. remain module-level constants, unchanged in structure — `GRAPH_SYSTEM_PROMPT` gained the bundle-contract section (a one-time content addition, still byte-stable across every call thereafter, consistent with what "byte-stable across calls" has always meant here).
- No changes to import, extraction, knowledge-layer, or embedding passes.

## Not run

Item 5 (21-question catalogue) and item 6 (`SEARCH` clause migration) — both explicitly conditioned on item 3 showing the expected reduction, which it did not for `graph_agent`. Running the catalogue at the current per-question cost (207,010 tokens on a search-routed question, likely 400k+ on any question that triggers a retry) would multiply the "more than a few dollars" concern from the previous report, not resolve it.
