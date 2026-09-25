# Session Handoff (2026-09-25)

Handoff for the next Claude session. **Next topic: build the AI agents** that answer questions over the graph: the
`Configure AI agent` tab (settings and LangGraph agent flows) and a new chat agent behind `Chat with AI`.

Read first: `AGENTS.md`, then `docs/EMBEDDING_LAYER_HANDOFF.md` (sections 1, 4, 5, 7 and 12 matter most for the agent),
then `docs/GRAPH_DATA_HANDOFF.md`. Each layer has its own `docs/*_HANDOFF.md`.

## Working with this user

- Talk Swedish. Write code, comments, docs and commit messages in English (`AGENTS.md`).
- **Never install anything without asking twice.** Global rule, no exceptions.
- **Commits:** the user (Hassan Mehdi) is the only author. No `Co-Authored-By` line, English message, push to `main`.
  PowerShell 5.1 splits inline messages with quotes, so write the message to a scratchpad file and use
  `git commit -F <file>`. Only commit or push when asked.
- The user is careful about the **build code** (anything that writes to Neo4j) and the **graph panel** (left box). It
  breaks easily. Prefer read-only changes; for the write path, explain the risk and ask first. Keep graph panel changes
  minimal and isolated.
- "Vad tycker du" / "visa först" means propose with a recommendation and wait. "Kör" means implement.
- Keep data-specific names (a person, an issue) out of fixed UI captions, since data changes on rebuild.
- Verify after every change: `frontend\node_modules\.bin\tsc.cmd --noEmit -p .` (run from `frontend`),
  `.\.venv\Scripts\python.exe -m py_compile <file>`, and hit the API. Say plainly what was not checked in the browser.
- Running the embedding build or any LLM layer calls OpenAI (cost) and writes to Neo4j: ask before running it. Small
  read-only checks and test questions are fine when the user has agreed to them.

## Running the app

- Start: `scripts\run_app.ps1` (backend + Vite), or `scripts\run_backend.ps1` and `npm run dev -- --host 127.0.0.1` in
  `frontend`. Backend on port 8000, Vite on 5173 (proxies `/api`), SQL viewer on 5000.
- **The Flask auto-reloader is off.** After editing backend code, restart the backend: find the process on port 8000
  (`Get-NetTCPConnection -State Listen -LocalPort 8000`), stop the tree (`taskkill /PID <id> /T /F`), start
  `scripts\run_backend.ps1` again.
- **Lesson from this session:** when a change touches both backend and frontend, restart the backend *before* the
  frontend change is saved. Vite hot-reloads at once, and a new button calling an old backend fails (the graph panel
  then shows "Neo4j disconnected" for any API error). After a backend restart, tell the user to press F5.
- Read-only graph inspection: `.\.venv\Scripts\python.exe scripts\inspect_neo4j.py`. Ad hoc read-only scripts go in the
  scratchpad, reuse `neo4j_config` / `run_read_query` from `scripts/inspect_neo4j.py`, and open sessions with
  `default_access_mode="READ"`. Never print `.env` or connection strings.
- PostgreSQL (read-only): `C:\Program Files\PostgreSQL\18\bin\psql.exe` with values from `.env` at runtime and
  `PGOPTIONS=-c default_transaction_read_only=on`.

## The project in one paragraph

Simulated source material from a software team (mail, Slack, Teams transcripts, issues, documents, PRs; 10 SQL tables
in PostgreSQL, the source of truth) is imported into Neo4j and enriched by layers into a multi-layer memory graph. An
AI agent should then answer questions over it: who discussed a decision before it was made, which documents explain a
requirement, which sources describe the same event, which people, issues, meetings and code changes belong together,
and where information is missing, changed or contradictory. The data tells one story: administrator sessions expiring
too early (`AUTH-17`), a first fix rejected by security review, a web-only fix, and a mobile regression (`AUTH-19`).

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
Embeddings depends on all earlier layers.

Current graph (2026-09-25): 107 nodes and 488 relationships in `Full graph`; 79 embedded nodes, all up to date.

## Tab conventions (for the new `Configure AI agent` tab too)

Established across all layer tabs; the Embeddings tab follows them as well:

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

## What was done this session

- Center tabs renamed and reordered: `Build graph layers` · `Configure AI agent` (new, **empty**) · `Chat with AI`.
  The default open tab is still `Chat with AI` (state key `"message"`; the others are `"notes"` and `"agent"`, in
  `App` in `frontend/src/main.tsx`).
- **Embedding layer rebuilt (`embedding-v3`)**. Full design and checks: `docs/EMBEDDING_LAYER_HANDOFF.md`.
- Graph panel: a `Chunks` filter button in the bottom row (left), key `Embeddings` -> `CHUNK_OF`; the `Searchable`
  label is never used as a node type; the `embedding` vector is listed last in the selection panel.
- Committed and pushed as `4695236`.

## Embedding layer: what the agent builds on

- **Model:** OpenAI `text-embedding-3-large`, 1536 dimensions, cosine similarity. A query must be embedded with the
  same model and dimensions.
- **15 embedded labels, every layer has an entry point:** the source retrieval units plus `CodeChange`, `Topic`,
  `Event`, `Component`, `RootCause`, eligible `Person` profiles and `Community`. Knowledge that exists only as
  relationships or numbers (causal explanations, dependencies, expertise shares, collaboration, communities, bus factor)
  is written into the node texts. Mailbox and ambiguous persons (`Support`, `Anna`) are excluded.
- **Every embedded node** has the label `Searchable` and the properties `embedding_text` (exactly what the vector
  represents), `embedding_group` (the source it belongs to; versions share a group), `embedding_is_latest`,
  `embedding_parts`, `embedding_model`, `embedding_version`, `embedded_at`.
- Long texts (over 12 000 characters) are split; parts 2..n are `EmbeddingChunk:Searchable` nodes linked
  `(:EmbeddingChunk)-[:CHUNK_OF]->(source)`. None exist with the current data. A hit on a chunk must be followed to its
  source.
- **Indexes:**

  | Index | Type | Use |
  | --- | --- | --- |
  | `searchable_embedding` | vector on `:Searchable(embedding)` | search by meaning, all layers in one ranking |
  | `searchable_text` | fulltext on `:Searchable(embedding_text)` | exact words: issue keys, PR numbers, file names, names |
  | `entity_lookup` | fulltext on `Person`/`Issue`/`Document`/`PullRequest`/`Topic` names and titles | resolve a name to a node |
  | `issue_key_lookup` | fulltext on `Issue.issue_key` | resolve an issue key |

- **Semantic test (section 12 of the embedding handoff):** vector search alone found an expected node in the top 5 for
  5 of 7 layer questions; with fulltext added, 7 of 7. Lessons for the agent:
  - Use **hybrid search** (vector + `searchable_text`) and merge the results (for example reciprocal rank fusion).
  - **Read the hit's `embedding_text`**: a component or topic text often holds the answer about people and risk.
  - **Ranking questions** ("lowest bus factor", "highest betweenness", "how many ...") need a direct Cypher query on
    the metric properties, not search.
  - Use `embedding_group` / `embedding_is_latest` to fold versions of one source into one hit, or to search only the
    latest versions.
  - `db.index.vector.queryNodes` works but is **deprecated** in the installed Neo4j; new code should use the Cypher
    `SEARCH` clause. Check the syntax against the installed version before relying on it.

## Chat agent

**Update (2026-09-25):** the agent was rebuilt as `backend/ai_agent/` (plan-and-execute with parallel layer
specialists) and the old `backend/langgraph_agent/` was removed. Design, settings, test questions and checks:
`docs/AI_AGENT_HANDOFF.md`. The notes below on cost causes and design considerations were the starting point.

## The user's goals for the next session

In the user's words, the next session should:

1. **Set up a suitable agent structure.**
2. **Look at how the AI traverses and fetches from the graph, or alternatively chooses to run a Neo4j search**, and
   decide when each is used.
3. **Cost.** The last time the user used the existing agent, the costs were very high. That is not acceptable, so
   cost must be designed in from the start, not fixed afterwards.
4. **Look at the AI's flow and whether it can be controlled more**, for example from the `Configure AI agent` tab.

## Likely causes of the high cost (read from the existing code)

- **Everything is resent on every tool call.** The agents run a tool loop (`MAX_TOOL_ITERATIONS = 4` in `nodes.py`),
  and each iteration resends the conversation, the system prompt and all earlier tool results. Cost grows faster than
  the number of steps.
- **Large tool results.** Up to `TOOL_OUTPUT_CHAR_BUDGET = 40_000` characters per tool result go into the context.
- **The whole graph schema is in the graph agent's system prompt** (`GRAPH_SCHEMA_REFERENCE` in `prompts.py`), sent on
  every iteration.
- **Several LLM steps per question:** orchestrator, search agent, graph agent and synthesis, each re-reading much of
  the same material.
- **A more expensive model for the graph agent** (`GRAPH_MODEL = "gpt-5.6-sol"`; the others use `gpt-5.6-terra`).
- **History:** `HISTORY_TURNS = 10` earlier turns are sent with every question.

## Things to consider when building the agents

1. **Let code fetch, let the AI think.** The cheapest and most predictable structure lets ordinary code do the
   retrieval: hybrid search, fixed traversals through the layers, and ranking queries in Cypher. The LLM is called only
   to interpret the question and to write the answer. Free Cypher written by the AI is a fallback only. This usually
   cuts cost sharply compared with letting the AI search on its own in loops.
2. **Cost control built in from the start.**
   - Measure tokens and cost **per question and per step**. The old code already sums token usage per role
     (`_merge_usage` in `nodes.py`); show it in the tab.
   - A **budget per question** with a hard cap: the agent stops instead of continuing.
   - A cheap model for routing and summarizing; an expensive one only where it makes a difference.
   - Keep the start of every prompt stable (system prompt, tool definitions) so OpenAI's prompt caching applies and
     repeated calls are cheaper.
3. **Question types decide the route.** Different questions need different routes:
   - **Lookup** ("what is AUTH-19?"): the lookup indexes (`entity_lookup`, `issue_key_lookup`); no search needed.
   - **Meaning** ("why did mobile break?"): hybrid search, then the causal chain in the graph.
   - **Ranking** ("who has the lowest bus factor?"): a direct Cypher query on the metric properties.
   - **Small talk:** no graph work at all.
4. **Swedish questions against English data.** Vector search handles the language switch; fulltext search does not.
   Translate the question, or extract English keywords, before the fulltext search.
5. **Traceability and control** (fits the `Configure AI agent` tab):
   - A **trace per question**: which steps ran, which nodes were fetched, tokens, time and cost.
   - **Settings**: model per step, search depth, maximum number of steps, budget, which tools are on.
   - **Citations** in answers, so every claim points to a node in the graph.
6. **Test questions with expected answers.** Start from the seven questions in section 12 of
   `docs/EMBEDDING_LAYER_HANDOFF.md` and the project goals. Run them after every change and compare both answer quality
   and cost.
7. **Security.**
   - Cypher must be read-only; this already exists in `backend/cypher_guard.py`.
   - Timeouts on queries.
   - Text in mail and Slack is data, not instructions: a source text can contain something that looks like an
     instruction.
8. **Staleness.** If Embeddings or a layer is stale, the agent should say so in its answer instead of silently
   answering from old data. The staleness is available from `backend/pipeline_staleness.py`.

## Next steps (proposal; confirm the plan with the user first)

1. Agree on what the `Configure AI agent` tab should hold: agent settings (models, search depth, which tools are on)
   and a view of the LangGraph flow (the graph of agent nodes, and possibly a trace of a run).
2. Design the new agent on the embedding layer: hybrid search on `searchable_embedding` + `searchable_text`, then
   traversal; fixed tools per layer (causes and root causes, components and dependencies, expertise and bus factor,
   communities); read-only Cypher for the rest; answers cite source nodes.
3. Use the seven questions in section 12 of the embedding handoff, plus the project goals above, as the agent's first
   test set.
4. Write `docs/AI_AGENT_HANDOFF.md` alongside the work, like the layer handoffs.

## Open cleanup (the user wants this at the end)

- `docs/BESLUTAD_PROJEKTINRIKTNING.md` is outdated (says nine SQL tables, misses `issues`, wrong note on issue
  versioning). Its unique parts are the project goals and the work order. Proposed: move those into `README.md`,
  delete the file, and fix the link to it in `README.md` (line 46). Not done yet.
- `SESSION_HANDOFF.md` (this file) is committed so the next session finds it. Replace it with a new handoff at the end
  of the next session.
