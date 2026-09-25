# Session Handoff (2026-09-26)

Handoff for the next Claude session. **Next topic: keep working on the AI agent** (`backend/ai_agent/`, the tabs
`Configure AI agent` and `Chat with AI`). The agent architecture is built, tested and works well; the user wants to
continue on the same thing.

Read first: `AGENTS.md`, then `docs/AI_AGENT_HANDOFF.md` (the agent: design, nodes, state, settings, test questions,
model choice, open issue), then `docs/EMBEDDING_LAYER_HANDOFF.md` (sections 1, 4, 5, 7, 12) and
`docs/GRAPH_DATA_HANDOFF.md`. `docs/SCALING_HANDOFF.md` is a checklist for when the data grows. Each layer has its own
`docs/*_HANDOFF.md`.

## Working with this user

- Talk Swedish. Write code, comments, docs and commit messages in English (`AGENTS.md`).
- **Never install anything without asking twice.** Global rule, no exceptions.
- **Commits:** the user (Hassan Mehdi) is the only author. No `Co-Authored-By` line, English message. PowerShell 5.1
  splits inline messages with quotes, so write the message to a scratchpad file and use `git commit -F <file>`.
  **Commit only when asked, push only when asked** ("committa" alone means commit only). Check that `git add`
  succeeded and what is staged before committing (on 2026-09-26 a failed `git add` of an already deleted path left a
  commit with only part of the changes).
- The user is careful about the **build code** (anything that writes to Neo4j) and the **graph panel** (left box). It
  breaks easily. Prefer read-only changes; for the write path, explain the risk and ask first. Keep graph panel changes
  minimal and isolated.
- "Vad tycker du" / "visa först" / "koda inte" means propose with a recommendation and wait. "Kör" means implement.
  When asked for an opinion, give a clear recommendation with the reasons; the user values honest pushback.
- Mark proposals that are not decided as such in the docs ("not decided, proposal only") when the user asks to
  document a problem without fixing it.
- Keep data-specific names (a person, an issue) out of fixed UI captions, since data changes on rebuild.
- Verify after every change: `frontend\node_modules\.bin\tsc.cmd --noEmit -p .` (run from `frontend`),
  `.\.venv\Scripts\python.exe -m py_compile <file>`, and hit the API. Say plainly what was not checked in the browser.
- Anything that calls OpenAI costs money: the layer builds (they also write to Neo4j; ask first), agent test runs and
  test questions (fine when the user has agreed to that work; report the cost).

## Running the app

- Start: `scripts\run_app.ps1` (backend + Vite), or `scripts\run_backend.ps1` and `npm run dev -- --host 127.0.0.1` in
  `frontend`. Backend on port 8000, Vite on 5173 (proxies `/api`), SQL viewer on 5000. Neo4j runs in Neo4j Desktop 2
  (Bolt 7687); the user starts it.
- **The Flask auto-reloader is off.** After editing backend code, restart the backend: find the process on port 8000
  (`Get-NetTCPConnection -State Listen -LocalPort 8000`), stop the tree (`taskkill /PID <id> /T /F`, the parent of the
  listening process is the venv launcher), start `scripts\run_backend.ps1` again. Two `python.exe` per backend is
  normal (the venv launcher starts the real interpreter).
- **Prompts and module constants are read when the backend starts.** A change to `ai_agent/prompts.py` (or any agent
  code) is not active until the backend is restarted; `settings.json` is the exception (read on every question). A
  test run made before the restart does not test the change (this happened on 2026-09-26).
- When a change touches both backend and frontend, restart the backend *before* the frontend change is saved. Vite
  hot-reloads at once. After a backend restart, tell the user to press F5.
- Read-only graph inspection: ad hoc scripts in the scratchpad, reuse `neo4j_config` from `scripts/inspect_neo4j.py`,
  open sessions with `default_access_mode="READ"`, run them with `.\.venv\Scripts\python.exe`. Never print `.env` or
  connection strings. Server-side connection check: `CALL dbms.listConnections()` and `SHOW TRANSACTIONS`.
- PostgreSQL (read-only): `C:\Program Files\PostgreSQL\18\bin\psql.exe` with values from `.env` at runtime and
  `PGOPTIONS=-c default_transaction_read_only=on`.
- Installed: Neo4j 2026.08.1 (Cypher `SEARCH` and scoped `CALL (x) { ... }` work), LangGraph 1.2.11, openai 3.16.2,
  neo4j driver 6.3.1.

## The project in one paragraph

Simulated source material from a software team (mail, Slack, Teams transcripts, issues, documents, PRs; 10 SQL tables
in PostgreSQL, the source of truth) is imported into Neo4j and enriched by layers into a multi-layer memory graph. An
AI agent answers questions over it: who discussed a decision before it was made, which documents explain a
requirement, which sources describe the same event, which people, issues, meetings and code changes belong together,
and where information is missing, changed or contradictory. The data tells one story: administrator sessions expiring
too early (`AUTH-17`), a first fix rejected by security review, a web-only fix, and a mobile regression (`AUTH-19`).
No new SQL tables are planned; node and relationship types are fixed in code, so more data never brings new types.

## Pipeline and layers

All layers are behind the center tab `Build graph layers`. Order and staleness live in `backend/pipeline_staleness.py`.

| # | Layer (tab name) | Code / key | Kind | Creates |
| --- | --- | --- | --- | --- |
| 0 | Import (SQL viewer) | `viewer/app.py` | deterministic | Source nodes, shared `Person` identities |
| 1 | Reference extraction | `reference_extraction.py` / `references` | regex | `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST`, `MENTIONS_DOCUMENT` |
| 2 | Knowledge layer | `topic_event_extraction.py` / `knowledge` | LLM | `Topic`, `Event`, `CAUSED`, `EVIDENCED_BY`, `ACTED_IN_EVENT`, ... |
| 3 | Architecture layer | `architecture_layer.py` / `architecture` | deterministic + LLM | `Repository`, `Module`, `File`, `Component`, `DEPENDS_ON`, ... |
| 4 | Root cause & impact layer | `causal_layer.py` / `causal` | LLM | `RootCause`, `CONTRIBUTED_TO`, `AFFECTED_COMPONENT`, ... |
| 5 | Expertise & collaboration layer | `collaboration_layer.py` / `collaboration` | deterministic | `Expertise`, `WORKS_WITH` |
| 6 | Graph algorithms | `graph_algorithms.py` / `algorithms` | networkx | `Community`; metrics on `Person`, `Topic`, `Component` (betweenness, bus factor, ...) |
| 7 | Embeddings | `embedding_pass.py` / `embeddings` | OpenAI embeddings | Vectors and search indexes on nodes of every layer |

Every layer stamps its nodes and relationships with `generated_by` and deletes only its own data on rebuild. LLM
layers do not give identical output on rebuild. Rebuilding an upstream layer makes everything downstream stale;
Embeddings depends on all earlier layers. Current graph: 107 nodes, 488 relationships, 79 embedded nodes, all up to
date. The agent's test questions name events, root causes and components; update them after an LLM layer rebuild.

## The AI agent (state on 2026-09-26)

Full description: `docs/AI_AGENT_HANDOFF.md`. In short:

- **Pattern:** plan-and-execute with parallel fan-out (LangGraph orchestrator-worker), no loops:
  `prepare -> planner -> entry -> {sources, causes, architecture, people} in parallel (Send) -> check -> [explorer] ->
  answer`; small talk goes `planner -> answer`. The planner decides once; there is no supervisor loop.
- **Code fetches, models think:** `entry` finds entry points in code (exact lookup, vector search on
  `searchable_embedding`, fulltext on `searchable_text`, reciprocal rank fusion, one hit per `embedding_group`).
  Each specialist walks its layers in fixed Cypher, then may make one bounded model follow-up with three tools of its
  own. `check` (code) judges whether evidence exists; the explorer (model + read-only Cypher through `cypher_guard`)
  runs at most once when it does not. The answer is one streamed call with citations.
- **Specialists cover every layer:** `sources` = import + references; `causes` = Knowledge + Root cause; `architecture`
  = Architecture; `people` = Expertise + Graph algorithms. Entry search covers every embedded label.
- **Models (chosen on the test set):** `gpt-4o` for the planner and the answer, `gpt-4o-mini` for the specialists and
  the explorer. 14 of 14 test questions pass at about $0.008 per question. Cheap planners misroute; a cheap answer
  model misattributes statements.
- **Cost control:** every model call is measured (tokens, cached tokens, cost); prices in `settings.json` were checked
  against OpenAI's pricing page on 2026-09-26 (Standard tier); budget per question; caps on every model step.
- **Neo4j sessions:** one driver per question, every session in a `with` block, one session per parallel specialist;
  checked on the server that nothing stays open (at most 2 connections during a question, 0 after).
- **`Configure AI agent` tab:** the flow drawn from the compiled graph (nodes, edges, state; the last question's path
  highlighted with time and cost per node), node details, the state table, the last run, the test questions (run with
  confirmation, results and history), and the settings form (saved through `POST /api/ai/agent/settings`, validated).
- **Chat:** stays mounted when another tab is shown (the conversation is kept); a page reload starts a new
  conversation (thread id per page load).

### Decisions

- **No loop from `check` back to the planner**, not even switched off behind a setting: an extra round does not
  guarantee a better answer and can run long on a large graph. When answers fall short, improve the planner and the
  specialists and measure with the test set.
- **Running without data is not a case to optimise** (like driving without fuel): the layers and embeddings are built
  before questions are asked. The staleness warning and the refusal on fetch errors already cover it.
- Four specialists grouped by kind of question, not one per layer (the paired layers share relationships, and fewer
  specialists means fewer model calls and easier routing).

### Open issue (not decided)

Questions outside the project (for example "Vad är Sveriges huvudstad?") run all four specialists, because small talk
is narrowly defined and the planner's safety rule runs every specialist when none is named. Documented with three
proposals in `docs/AI_AGENT_HANDOFF.md`, section 11. The user has not decided to fix it.

## What was done on 2026-09-25 and 2026-09-26

- **Agent built from scratch** (`backend/ai_agent/`), the old `backend/langgraph_agent/` removed. Steps: skeleton with a
  cheap model; specialist follow-ups and the explorer; the `Configure AI agent` tab; test questions; editable settings;
  model comparison. Fixes found by testing: query timeout in read transactions (`unit_of_work`), citation format,
  meeting participants and mail recipients as citable evidence in `people`, relationship types with a colon in the
  explorer schema, planner rules for small talk and ranking, answer rule for attributing quoted lines.
- **Graph panel:** an `Entry points` toggle (bottom row, next to `Chunks`) rings every embedded node in the shown
  filter. The earlier `TODO.md` (which proposed an `Embedded` filter instead) was removed.
- Minor web design: a tree instead of brackets in the right panel's layer map; readability of the agent drawing.
- New docs: `docs/AI_AGENT_HANDOFF.md`, `docs/SCALING_HANDOFF.md`.

## Where to continue (proposals; confirm with the user)

- Improve the planner and the specialists where answers fall short, and grow the test set (more questions, several runs
  per question to see the spread). Weak points seen: the follow-up model sometimes passes a Swedish word as a tool
  argument; Swedish answers are somewhat stiff with `gpt-4o`.
- Cost: most of the cost is the answer call (it reads all the evidence); `evidence.max_text_chars` and
  `max_nodes_per_specialist` are the levers, to be measured with the test set.
- The open issue above, if the user decides to fix it.

## Background: the user's original goals for the agent (2026-09-25) and how they were met

1. **A suitable agent structure** -> plan-and-execute with parallel layer specialists.
2. **When the AI traverses the graph and when it runs a Neo4j search** -> search only at the entry (hybrid), fixed
   traversals per layer in code, direct Cypher for ranking questions, model-written Cypher only in the capped explorer.
3. **Cost** (the old agent was very expensive) -> designed in: code retrieval, one planner call, one answer call,
   bounded follow-ups, measured per node, budget per question.
4. **Control of the flow** -> the `Configure AI agent` tab (drawing, trace, settings, tests).

Why the old agent was expensive (lessons that still apply): tool loops that resent the whole conversation and all tool
results on every iteration, tool results of up to 40 000 characters, the whole graph schema in a system prompt, four
model steps re-reading the same material, the most expensive model in the widest loop, ten history turns per question.

Design principles the agent follows (from the first handoff): let code fetch and the model think; cost control from the
start (measure per question and step, budget, cheap models where they suffice, stable prompt starts for caching);
question types decide the route (lookup, meaning, ranking, small talk); translate or extract English keywords for
fulltext on English data; trace, settings and citations; test questions with expected answers; read-only Cypher,
timeouts, source text treated as data, never instructions; say when a layer is stale.

## Embedding layer: what the agent builds on

- **Model:** OpenAI `text-embedding-3-large`, 1536 dimensions, cosine similarity; a query must use the same model.
- **15 embedded labels, every layer has an entry point:** the source retrieval units plus `CodeChange`, `Topic`,
  `Event`, `Component`, `RootCause`, eligible `Person` profiles and `Community`. Knowledge that exists only as
  relationships or numbers is written into the node texts. Mailbox and ambiguous persons (`Support`, `Anna`) are
  excluded.
- **Every embedded node** has the label `Searchable` and `embedding_text`, `embedding_group`, `embedding_is_latest`,
  `embedding_parts`, `embedding_model`, `embedding_version`, `embedded_at`. Long texts are split into
  `EmbeddingChunk:Searchable` nodes (`CHUNK_OF` to the source); none exist with the current data.
- **Indexes:** `searchable_embedding` (vector, meaning, all layers), `searchable_text` (fulltext, exact words),
  `entity_lookup` (names and titles), `issue_key_lookup`.
- `db.index.vector.queryNodes` is deprecated; the agent uses the Cypher `SEARCH` clause.

## Tab conventions

Established across all tabs, `Configure AI agent` included:

1. Description line under the button/status row: `<p className="reference-description">`.
2. Counts headings (`reference-description reference-counts-heading`), split by meaning: what the layer creates vs
   what it only reads or writes onto existing nodes.
3. Run metrics only right after a run (`justRan`).
4. Table headings `knowledge-card-title knowledge-section-title` with a lighter suffix
   `<span className="knowledge-section-kind">(...)</span>`; the same span on column labels.
5. Captions `knowledge-table-caption` (a `div` with one `<p>` per block and `<strong>` block labels for longer ones).
6. Layout helpers: `reference-cell-wrap`, `reference-cell-center`, `layer-last-table`,
   `reference-table-wrapper-capped`; many-table panels join the `grid-auto-rows: max-content` group in `styles.css`.
7. Keep the matching `docs/*_HANDOFF.md` updated with every change.

## Open cleanup (the user wants this at the end)

- `docs/BESLUTAD_PROJEKTINRIKTNING.md` is outdated (says nine SQL tables, misses `issues`, wrong note on issue
  versioning). Its unique parts are the project goals and the work order. Proposed: move those into `README.md`,
  delete the file, and fix the link to it in `README.md` (line 46). Not done yet.
- Small outdated statements found in the layer docs: `REFERENCE_EXTRACTION_LAYER_HANDOFF.md` and
  `KNOWLEDGE_LAYER_HANDOFF.md` say the middle panel has three inner tabs (it has seven); `KNOWLEDGE_LAYER_HANDOFF.md`
  says "the five CAUSED links" in one place and 6 elsewhere; `GRAPH_ALGORITHMS_HANDOFF.md` refers to
  `NEW_LAYERS_WORK_ORDER.md`, which does not exist. Not fixed yet.
- `SESSION_HANDOFF.md` (this file) is committed so the next session finds it. Replace it with a new handoff at the end
  of the next session.
