# AI Agent Handoff

The graph question-answering agent behind `Chat with AI`, and its view in `Configure AI agent`. Implemented in
`backend/ai_agent/`, exposed by `backend/app.py` (`POST /api/ai/chat`, `GET /api/ai/agent`,
`POST /api/ai/agent/settings`, `GET|POST /api/ai/agent/evaluation`), used by `ChatPanel` and `ConfigureAgentPanel` in
`frontend/src/main.tsx`.

**Verified on 2026-09-30** against the code in `backend/ai_agent/` (graph, nodes, specialists, follow-up, settings,
test questions), `backend/ai_agent/evaluation_last.json` and the frontend constants, on the Kvitta data. The agent
reads the graph only; it depends on every layer (`docs/PIPELINE_AND_LINKS_HANDOFF.md`).

**Status.** Built and working. Models (`settings.json`): planner, explorer and answer `gpt-6-sol`; specialists and
summary `gpt-6-luna`; the answer step at reasoning effort `medium`. The 27 test questions for the Kvitta data (the ten
demo questions, layer checks, chunk questions, small talk and nine control questions) all pass; a full run costs
about $0.40 (section 9).

## 1. Design principles

- **Let code fetch, let the model think.** Retrieval (lookup, hybrid search, walking the layers) is ordinary code. A
  model is called to understand the question (planner), for one bounded follow-up per specialist, as a fallback
  explorer, to summarise long conversations, and to write the answer.
- **Plan once, no supervisor loop.** The planner decides once which specialists run; no model call decides the next
  step. Cost per question is predictable. (Decided: no loop from `check` back to the planner.)
- **Specialists follow the graph's layers.**
- **Every model step is capped** (rounds, tool calls, extra nodes, rows, characters) and a budget per question stops
  the optional steps.
- **Every node and edge is declared**, so the compiled graph can be drawn completely in `Configure AI agent`.

## 2. Flow

Plan-and-execute with parallel fan-out (LangGraph orchestrator-worker), with one corrective step:

```text
START -> prepare -> summarize -> planner -+-> answer                                   (small talk)
                                          +-> entry -+-> sources      -+
                                                     +-> causes       -+-> check -+-> answer -> END
                                                     +-> architecture -+          +-> explorer -> answer
                                                     +-> people       -+
```

`entry` starts the chosen specialists with `Send`; they run in parallel and each appends its packet to `evidence`.
`check` runs once after all of them. `entry` goes straight to `check` when there are no entry points or no
specialists.

## 3. Nodes

| Node | Kind | Does |
| --- | --- | --- |
| `prepare` | code | Loads `settings.json`, computes layer staleness, splits the stored conversation into the last `history_turns` turns (word for word) and the turns still to be summarised |
| `summarize` | model (1 call, only when turns left the window) | Folds those turns into the running summary (at most `conversation_summary.max_chars`); on failure keeps the old summary and retries the same turns next time |
| `planner` | model (1 call, structured `PlanOut`) | `standalone_question` (the question rewritten to stand on its own), `question_types` (`smalltalk`, `lookup`, `why`, `ranking`, `who`, `timeline`, `impact`, `other`), `language`, `entities`, `keywords_en`, `specialists`, `reason`. Route `smalltalk` only when the types are exactly `["smalltalk"]`. **Safety rule:** a graph question with no usable specialist runs every enabled specialist |
| `entry` | code + 1 embedding call | Searches with the standalone question: exact lookup (issue keys, PR refs, document identifiers; names via `entity_lookup`), vector search (`searchable_embedding`, Cypher `SEARCH`), fulltext (`searchable_text`, on the English keywords); merged by reciprocal rank fusion (`RRF_K = 60`), best hit per `embedding_group`, at most `search.entry_points`. Also stores the question vector |
| `sources` | code + model follow-up | Source records around the entry points: the entry nodes, versions, comments, reviews and code changes of an entry issue, document or PR, the evidence of entry events, and nodes that mention an entry issue, PR or document. Plus: the part of a pull request an entry source names (a reserved place, below), and the **topic sources** fact |
| `causes` | code + follow-up | Events, root causes, topics: entry events, events citing entry sources, root causes of entry events and events of entry root causes, events an entry code change contributed to, root causes in an entry component, events of an entry issue's topic, events affecting an entry component. Plus the **causal chain** facts |
| `architecture` | code + follow-up | Components: entry components; components affected by entry events, holding entry root causes, evidenced by entry sources, implemented in files an entry code change or PR modifies; dependency neighbours |
| `people` | code + follow-up | Eligible persons and communities: entry persons, actors of entry events, people linked by activity to entry sources, experts on entry subjects, community members, meeting participants, mail recipients; participation lists of an entry meeting or mail as a citable item; always the **outside persons** and **groups** facts; for `ranking` questions the metric tables as facts and "what X's knowledge rests on" facts; for a general `who` question (no entity) the list of every person as a fact |
| `check` | code | Not enough when: no entry points; no evidence; a `why` question where `causes` ran and found no nodes; a `ranking` question without any facts. Otherwise enough |
| `explorer` | model + read-only Cypher | Runs at most once, only when `check` says not enough, the explorer is enabled and the budget allows |
| `answer` | model (1 streamed call) | Answers from the evidence only, cites references in square brackets, mentions stale layers, answers in the planner's language. **Refuses in code** (no model call) when any fetch failed |

Every node emits a status line to the chat as it starts and appends one `trace` entry (time, summary) through the
`traced` wrapper.

**Evidence selection inside a specialist.** Candidates get a priority (0 = the entry point itself, 1 = one step away,
2 = further, 3 = added by the follow-up). The best `evidence.max_nodes_per_specialist` are kept; among equal priority,
the ones most similar to the question (cosine between the question vector and the node's stored `embedding`, computed
in Neo4j) come first; nodes without an embedding (`Issue`, `Document`, `TeamsMeeting`) after those with one; then
time. Kept nodes are listed in time order with their `embedding_text` cut to `evidence.max_text_chars`; nodes without
an embedded text get a short fallback text.

**Chunks.** When search reached a document version through chunks of its long text (`EmbeddingChunk`,
`docs/EMBEDDING_LAYER_HANDOFF.md`, section 5), the entry point keeps the ids of those chunks (`chunk_ids`), and the
node's evidence text is the text of those chunks in order, so the model reads the part that matched rather than the
beginning of the document.

**Reserved place for a named pull request** (`referenced_parts`, `sources` only). When an entry source names a pull
request ("in my review of kvitta-mobile#6"), the review or code change of that PR most similar to the question takes
one of the `max_nodes_per_specialist` places, replacing the lowest-ranked node. A reply is replaced by the review it
answers, where the statement was made. At most `MAX_REFERENCE_PLACES` = 1, only at a similarity of at least
`MIN_REFERENCE_SIMILARITY` = 0.64 (measured on the Kvitta data).

**Facts added by the specialists** (one line each, cited as `fact-N`):

| Fact | Specialist | Content |
| --- | --- | --- |
| Topic sources (`topic_source_facts`) | `sources` | For each entry topic (or, with none, the topic most entry points belong to): every source it was built from, by kind (issues, pull requests, documents, mails, Slack messages, meetings), and the mails and Slack messages that name no issue but are close to the topic in meaning (cosine to the topic's embedding at least `UNLINKED_SIMILARITY` = 0.78) and time (within its events' period, one day either side), with author and the first 160 characters, at most 5 |
| Causal chain (`causal_chain_facts`) | `causes` | For the events in the packet that no other listed event led to: every chain of `CAUSED` and `CROSS_TOPIC_CAUSED` links of up to three steps ending there, oldest step first with dates; the events most similar to the question first, at most `MAX_CHAIN_FACTS` = 2 |
| Outside persons (`outside_person_facts`) | `people` | Every eligible person whose e-mail domain differs from the domain most persons share (the customer), with every mail they sent, replies folded into their thread by subject |
| Groups (`community_facts`) | `people` | Every community from the graph-algorithms layer with its members |

**Citations.** In the answer's evidence text every node starts with its reference in brackets (its name), and every
distinct fact gets `fact-1`, `fact-2`, ... The answer's brackets are resolved against these: a node becomes a
citation with its label, a fact a citation labelled `Fact`; anything else is reported as unresolved.

## 4. Specialist follow-up and explorer (`followup.py`)

**Follow-up.** After the code fetch, one model call (`models.specialists`) sees the question and a compact view of the
packet (label, name, 160-character snippet and the node's `Mentions:` line per node). It replies DONE or calls up to `max_calls_per_round` of its own
tools; `max_rounds` rounds (1: results are not sent back). At most `max_extra_nodes` new nodes are added, most similar
to the question first. Skipped when the budget is spent. A failed follow-up is not an error.

| Specialist | Tools (fixed, parameterised Cypher; names resolved case-insensitively) |
| --- | --- |
| `sources` | `get_timeline(reference)`, `get_conversation(reference)` (Slack thread, mail reply chain, or a segment's meeting), `search_sources(query)` |
| `causes` | `get_causal_chain(event)` (up to three causal steps), `get_root_causes(subject)`, `search_causes(query)` |
| `architecture` | `get_component(component)`, `get_file_history(path)`, `search_architecture(query)` |
| `people` | `get_person(name)`, `get_experts(subject)` (shares, ranks and what each expert's knowledge rests on), `rank(metric)` (betweenness, weighted degree, bus factor) |

**Explorer.** A model (`models.explorer`) with one tool, `run_cypher`. Every query passes
`cypher_guard.enforce_read_only` (rejects writes, adds a `LIMIT` of `max_rows`) and runs in a read transaction with
the query timeout. At most `max_queries` queries; each result is cut to `max_rows` rows and `max_result_chars`
characters, with a note when it was cut; errors are shown to the model so it can correct itself. Element ids in the
rows become evidence nodes (at most `max_extra_nodes`); the rows become facts, filled from the latest query backwards
within `max_result_chars`. Its prompt holds a compact fixed schema of labels, key properties and every relationship
type.

**What expertise rests on** (`expertise_basis`). For each expert ranked 1 to `MAX_BASIS_EXPERTS` = 2 on a subject,
one fact listing the activities the Expertise layer counted (`EXPERTISE_EVIDENCED_BY`) and how the person is linked
to each, weightiest kinds first, at most `MAX_BASIS_ACTIVITIES` = 6 then `... and N more`. `people` adds these for
ranking questions (every topic and component); `get_experts` for one subject.

**Every person** (`person_roster`). For a `who` question that names no entity, one fact listing every person: emails
(another domain usually means another organisation), community, activity counts per kind (`ROSTER_PHRASES`), mails
received, subjects of up to two sent mails; mailboxes and ambiguous names listed apart as "Not counted as people". At
most `MAX_ROSTER` = 30 persons. The answer prompt asks to tell project members from outsiders, to say "all" or a
total only when the evidence holds a complete list, and not to agree with a user's correction unless the evidence
shows the answer was wrong.

## 5. State and conversations

`backend/ai_agent/state.py`, one question per run: `question`, `stored_history`, `conversation_summary`,
`summarized_messages`, `recent_history`, `pending_history`, `settings`, `staleness`, `plan`, `entry_points`,
`question_vector`, `evidence` (appended), `sufficiency`, `explorer_ran`, `final_answer`, `citations`,
`dropped_citations`, `usage` (appended), `trace` (appended), `errors` (appended).

Conversations are kept outside the graph in `graph.py`, per `thread_id` (at most `MAX_THREADS` = 200), with up to
`STORED_HISTORY_MESSAGES` = 24 messages, the running summary and how many messages it covers; a message is dropped
only after it is in the summary. Only the planner and the answer see the conversation; search, specialists and the
explorer see the standalone question. The conversation lives in backend memory: a backend restart forgets it.

## 6. Settings (`backend/ai_agent/settings.json`)

Read at the start of every question (an edit applies at once, no restart). Missing keys fall back to `DEFAULTS` in
`settings.py`. Editable from the tab (`POST /api/ai/agent/settings`, validated: known keys, ranges in
`EDITABLE_NUMBERS`, booleans, models only from those with a price, efforts only from `none`, `low`, `medium`, `high`;
prices only in the file).

| Key | Current |
| --- | --- |
| `models` | summarize `gpt-6-luna`, planner `gpt-6-sol`, specialists `gpt-6-luna`, explorer `gpt-6-sol`, answer `gpt-6-sol` |
| `reasoning_effort` | summarize `none`, planner `low`, specialists `low`, explorer `low`, answer `medium` (sent only to reasoning models: names starting `gpt-5`, `gpt-6`, `o1`, `o3`, `o4`). The answer step at `medium` follows the evidence and the facts more consistently from run to run |
| `history_turns` | 3 |
| `conversation_summary` | enabled, 1500 characters |
| `budget_usd_per_question` | 0.05 (above it, follow-ups and the explorer are skipped; the answer always runs) |
| `specialists` | all four on |
| `specialist_followup` | enabled, 1 round, 2 calls per round, 4 extra nodes |
| `explorer` | enabled, 3 queries, 25 rows, 4000 characters, 6 extra nodes |
| `search` | `vector_k` 10, `fulltext_k` 10, `lookup_k` 3, `entry_points` 8 |
| `evidence` | 8 nodes per specialist, 1200 characters per text |
| `query_timeout_seconds` | 30 |
| `prices_usd_per_million_tokens` | `gpt-4o` 2.5 / 1.25 / 10; `gpt-4o-mini` 0.15 / 0.075 / 0.6; `gpt-5.6-terra` 2.0 / 0.2 / 12.0; `gpt-6-luna` 0.1 / 0.01 / 0.5; `gpt-6-sol` 2.0 / 0.2 / 10.0; `text-embedding-3-large` 0.13 (input / cached input / output). Checked 2026-09-27 against OpenAI's pricing page, Standard tier. `gpt-6-sol` and `gpt-6-luna` have promotional prices (Sol until 2026-11-21): update them when the promotion ends, or cost is counted too low |

The query embedding always uses the embedding layer's model and dimensions (not a setting).

## 7. Neo4j sessions

One driver per question, closed when the question ends (also when the stream is interrupted). Every session is a read
session in a `with` block; each parallel specialist has its own session; every query runs in a read transaction with
the timeout. Checked 2026-09-25 on the server: at most 2 connections during a question, 0 after.

**Warm-up at start.** The first run of a query after Neo4j starts plans the query and reads its data from disk (up to
several seconds per query). When the backend starts, `warmup.py` runs the entry searches and all four specialists once
in a background thread, read only and without a model call, on a real node's embedding (waiting up to 5 minutes for
Neo4j). The backend prints `Agent warm-up done in N s.`; after that the first question is as quick as the rest.

## 8. API and frontend

`POST /api/ai/chat` `{"message", "thread_id"}`, streamed as Server-Sent Events (`Cache-Control: no-cache`,
`X-Accel-Buffering: no`): `status`, `token`, `tool_error`, `sources`, `done` (`answer`, `citations`,
`dropped_citations`, `token_usage`, `usage`, `cost_usd`, `plan`, `entry_points`, `sufficiency`, `trace`). A missing
key answers 503 before the stream starts.

`GET /api/ai/agent`: the compiled graph's nodes and edges, the state fields, the settings, the available models and
efforts (from `describe.py`, whose `NODE_INFO` must be kept in step with `nodes.py`).

`GET /api/ai/agent/evaluation`: test questions, last run, run history. `POST` runs every test question (calls
OpenAI), streamed: `progress` per question, then `done`. Results in `evaluation_last.json` and
`evaluation_history.json` (both ignored by git).

**Chat with AI** (`ChatPanel`): stays mounted when another tab is shown; the thread id, messages, unsent text and the
sound and reference choices are kept in the tab's `sessionStorage`, so a page reload keeps the conversation (the
backend keeps its history unless it was restarted). `New` starts a new thread. Answers are rendered as a small, safe
subset of Markdown and typed out at about `TYPING_CHARS_PER_SECOND` = 50, faster when far behind
(`TYPING_CATCH_UP_PER_SECOND` = 0.66), scrolling at most every `TYPING_SCROLL_EVERY_MS` = 120 ms. Under each answer,
the node citations are chips that find and ring the node in the graph panel; the fact citations are behind a
`Show facts (N)` button (`Hide facts` closes them again), one per answer, closed by default. The `[ ]` button shows or hides the bracket references in the text; the speaker
button turns a quiet typing blip on or off. While the AI works, the right panel's knowledge graphic glows and signals
run along its lines; afterwards the layers the answer drew on stay lit.

**Configure AI agent** (`ConfigureAgentPanel`): the flow drawn from the compiled graph (the last question's path in
amber with time and cost per node), node details, the state table (`Show state`), entry points of the last question,
the last run, the conversation's cost per question, the settings form, and the test questions (`Run test questions`
with a cost estimate; `Show questions and results`; run history).

## 9. Test questions (`backend/ai_agent/test_questions.json`)

27 questions for the Kvitta data, scored in code by `evaluation.py`: each has an expected route, groups of expected
evidence nodes (label plus exact name or part of the name), optional expected facts and expected words in the
answer; a question passes when all groups match. Every question runs without history. Expected nodes and words were
taken from the graph and checked against the SQL data. All 27 pass (run of 2026-09-29).

| Ids | What they test |
| --- | --- |
| q01-q10 | The ten demo questions in `kvitta/demofrågor.md`: the duplicate payout's cause across two storylines, the early warning in a review, who argued what before the offline decision, the sources of the Fortnox outage (including the two mails that name no issue), how the VAT requirement changed, the knowledge risks, the link to the customer and who collaborates most, the PR description against the code, what the go decision required and what was removed, and the customer's problems |
| q11-q15 | One layer each: the expert on the receipt reader, the groups, the highest betweenness, a component's dependencies, the participants of a meeting |
| q16-q17 | Chunks: answers found only in part 3 of `DESIGN-RECEIPT-READER` (the weekly figures to monitor, the date read from a hotel invoice) |
| q18 | Small talk (`Hej!`) |
| q19-q27 | Control questions in other words or about other storylines (the sources of the duplicate payout, why the Visma export was moved, what Anders Nyberg complained about, whether anyone warned of the Fortnox outage, how the team is divided, the messages of the offline debate, who reviewed Emma's first PR, the code change behind the spinner fix, how taxi receipts are read). They were never used to tune the agent |

A full run with the current settings takes about 6 minutes and costs about $0.40.

## 10. Extending the agent

- A new question type: add it to the planner's `question_types` and give `check` or a specialist a rule for it.
- A new fact: write a read-only query and a function in `specialists.py` that returns one line per item, and add it
  to the specialist's packet.
- New data: rewrite the test questions from the new graph (read-only queries), keep a set of control questions that
  are never used for tuning, and run each question more than once.
- Much larger data: see `docs/SCALING_HANDOFF.md` (query limits, ranking caps, roster size).

## 11. Code map

| File | Holds |
| --- | --- |
| `graph.py` | `build_graph`, `stream_chat`, conversation memory, `MissingApiKeyError`, `require_openai_client` |
| `nodes.py` | `traced`, `prepare`, `summarize`, `planner` (`PlanOut`), `entry`, `dispatch_specialists`, the four specialist nodes, `check`, `explorer`, `answer` (evidence text, citations) |
| `retrieval.py` | reference detection, lookups, vector and fulltext search (keeping the matched chunk ids), reciprocal rank fusion, group folding |
| `specialists.py` | the four specialists' queries, participation items, rankings, `expertise_basis`, `person_roster`, `referenced_parts`, `topic_source_facts`, `causal_chain_facts`, `outside_person_facts`, `community_facts`, `run_specialist` (with the matched chunk texts) |
| `followup.py` | follow-up tools, `run_followup`, `run_explorer` |
| `describe.py` | `NODE_INFO`, `EDGE_LABELS`, `STATE_DESCRIPTIONS`, `describe_agent` |
| `evaluation.py`, `test_questions.json` | scoring, `run_evaluation`, saved runs |
| `db.py` | `read` (read transaction with timeout), `fetch_nodes`, `node_text`, `chunk_texts`, `similarities` |
| `prompts.py` | `PLANNER_PROMPT`, `SUMMARY_PROMPT`, `ANSWER_PROMPT`, follow-up and explorer prompts |
| `settings.py`, `settings.json` | settings, defaults, validation |
| `warmup.py` | `warm_up`, `start_warmup` (called from `backend/app.py` at start) |
| `state.py`, `usage.py` | `AgentState`; usage and cost |
| `backend/cypher_guard.py` | read-only enforcement for model-written Cypher |

Prompts and module constants are read when the backend starts: after editing agent code, restart the backend
(`settings.json` is the exception).

## 12. Boundaries

- Reads Neo4j only (read sessions, read transactions); never writes to Neo4j or PostgreSQL.
- Model-written Cypher passes `cypher_guard` and runs in a read transaction: two independent guards.
- Source texts are data, never instructions (stated in every prompt).
- When a fetch fails, the answer states no facts (decided in code).
