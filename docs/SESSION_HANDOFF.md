# Session Handoff (2026-10-08)

Handoff for the next Claude session, checked against the code, both databases, the GraphRAG index and the server on
2026-10-08.

**Current state.** The Kvitta example data is loaded, all seven graph layers are built and up to date, the AI agent
answers all 27 test questions. **Microsoft GraphRAG** runs as a second RAG system next to it (index built, four
searches, evaluation done; `docs/GRAPHRAG_HANDOFF.md`). The work is on the branch `microsoft-graphrag` (pushed, not
merged into `main`). The server runs that branch (`DEPLOY_HANDOFF.md`).

## Documents

| Document | Content |
| --- | --- |
| `README.md` | What the project does, how to start it, where everything is |
| `AGENTS.md` (root) | Project rules: venv, scripts, database access, language |
| `kvitta/` | The Kvitta scenario in Swedish: product, storylines, team and systems, demo questions with answer keys |
| `docs/SQL_DATA_HANDOFF.md` | The six sources and ten tables: every column, constraint, JSON shape, identity and reference rule, import behaviour, DDL; the Kvitta rows |
| `docs/DATA_GENERATION_GUIDE.md` | What a dataset must contain per layer; validation queries |
| `docs/GRAPH_DATA_HANDOFF.md` | Every node label, relationship type, constraint and index in Neo4j; graph API and panel |
| `docs/PIPELINE_AND_LINKS_HANDOFF.md` | Build order, prerequisites, staleness, what rebuilds remove, paths through the graph, evidence identifiers |
| `docs/REFERENCE_EXTRACTION_LAYER_HANDOFF.md` ... `docs/EMBEDDING_LAYER_HANDOFF.md` | One per layer (References, Knowledge, Architecture, Root cause & impact (`CAUSAL_`), Expertise & collaboration (`COLLABORATION_`), Graph algorithms, Embeddings) |
| `docs/AI_AGENT_HANDOFF.md` | The chat agent: flow, nodes, facts, settings, test questions |
| `docs/GRAPHRAG_HANDOFF.md` | Microsoft GraphRAG: pipeline, index, settings and why, searches, cost limit, tabs, evaluation, server |
| `docs/SCALING_HANDOFF.md` | What to change for much larger data |
| `DEPLOY_HANDOFF.md`, `deploy/README.md` | The server: services, login, files that live only there, how to update it |

The file names `SQL_DATA_HANDOFF.md`, `EMBEDDING_LAYER_HANDOFF.md` and `AI_AGENT_HANDOFF.md` are referenced from code
comments (`viewer/person_identity.py`, `backend/embedding_pass.py`, `backend/ai_agent/__init__.py`); keep them.

## Working with this user

- Talk Swedish. Write code, comments, docs and commit messages in English (`AGENTS.md`); only `kvitta/` is Swedish.
- **Never install anything without asking twice.** Global rule, no exceptions.
- **Confirm before any change**: state the plan and wait for an explicit "ja". "Vad tycker du" / "visa först" /
  "koda inte" means propose with a recommendation and wait. "Kör" means implement. Build exactly what was agreed.
- **One thing at a time.** Finish it, report briefly, then the next. Keep answers short. When a step has several
  parts (service, backend, restart, frontend), list them first and wait for a yes; stop after the step and show the
  result. "Vänta" means stop now, also a process already started.
- **Everything runs from the app.** A build, an export, a question or a test run is a button in its tab; never run it
  from the command line instead (only read-only checks or diagnosis, said as such).
- Explain with the user's own terms (for GraphRAG: Config, Documents, Text units, Graph, Communities, Community
  reports, Embeddings), not with invented words. Separate what was checked from what is believed.
- **Prove the cause before changing code** (measure, query, read), then make one general fix; revert anything that
  did not work at once.
- **Commits:** the user (Hassan Mehdi) is the only author; no `Co-Authored-By` line; English message written to a
  scratchpad file and committed with `git commit -F <file>` (PowerShell 5.1 splits inline messages with quotes).
  Commit only when asked, push only when asked ("committa" alone means commit only).
- The user is careful about the **build code** (anything that writes to Neo4j) and the **graph panel** (left box):
  prefer read-only changes; for write paths explain the risk and ask first; keep graph panel changes minimal and
  isolated.
- Anything that calls OpenAI costs money: layer builds (they also write to Neo4j: ask first) and agent test runs.
  When checking agent changes, run only the questions concerned, through a script, before a full run.
- Verify after every change: `frontend\node_modules\.bin\tsc.cmd --noEmit -p .` (from `frontend`),
  `.\.venv\Scripts\python.exe -m py_compile <file>`, and hit the API.
- When a change touches backend and frontend, restart the backend before the frontend part is saved (Vite reloads at
  once); after a backend restart, tell the user to press F5.
- Documents describe what works and how; they do not list weaknesses.

## Running the app

- **Neo4j**: `scripts\run_neo4j.ps1` starts the project database that Neo4j Desktop installed, hidden in the
  background, without the Desktop app (Bolt 7687, HTTP 7474; Desktop's Java 21). `scripts\stop_neo4j.ps1` stops it.
  Log: `logs\neo4j.log` in the DBMS folder.
- **Backend**: `scripts\run_backend.ps1` (Flask, port 8000). Two `python.exe` per backend is normal (the venv's
  launcher and the interpreter). The auto-reloader is off (`BACKEND_RELOAD=1` in `.env` turns it on). After editing
  backend code: stop the process tree listening on 8000 and start the script again. Prompts and module constants are
  read at start; `backend/ai_agent/settings.json` is read per question.
- **Frontend**: `npm.cmd run dev -- --host 127.0.0.1` in `frontend` (Vite dev server on 5173, proxies `/api` to
  8000). `scripts\run_app.ps1` starts the GraphRAG service, the backend and Vite together (it waits up to 60 s for
  the backend) and stops them together when Vite stops.
- **Microsoft GraphRAG service**: `graphrag_service/server.py` in `.venv-graphrag` (Python 3.13), port 8100;
  `scripts\run_graphrag_service.ps1` starts it alone. Two `python.exe` per service is normal. After editing it, stop
  the process tree on 8100 and start it again; it warms up in the background at start.
- **SQL viewer** (port 5000, the import buttons): started from the app's `Open SQL Viewer` button or
  `scripts\run_viewer.ps1`.
- **PostgreSQL 18** runs as a Windows service (port 5432), database `hm_data`. Read-only access:
  `C:\Program Files\PostgreSQL\18\bin\psql.exe` with values from `.env` at runtime and
  `PGOPTIONS=-c default_transaction_read_only=on`. Never print `.env`.
- Read-only graph inspection: scratchpad scripts reusing `neo4j_config` from `scripts/inspect_neo4j.py`, sessions
  with `default_access_mode="READ"`, run with `.\.venv\Scripts\python.exe`.
- Installed: Neo4j 2026.08.1 Enterprise (Cypher `SEARCH`, scoped `CALL (x) { ... }`), PostgreSQL 18.6, langgraph,
  openai, neo4j driver, networkx, Flask, psycopg, pydantic (`requirements.txt`, `.venv`); `graphrag` 3.2.0 with its
  dependencies (`requirements-graphrag.txt`, `.venv-graphrag`, about 1 GB). The machine has 8 GB RAM.
- Read-only checks of the GraphRAG index: scratchpad scripts run with `.\.venv-graphrag\Scripts\python.exe` reading
  `graphrag_project/output` with pandas.

## The project in one paragraph

Simulated source material from a software team (mail, Slack, Teams transcripts, issues, documents, pull requests; ten
SQL tables in PostgreSQL, the source of truth) is imported into Neo4j and enriched by seven layers into a memory graph;
an AI agent answers questions over it. The data is the **Kvitta** scenario (`data/kvitta_seed.sql`, 263 rows): the team
of Kvitta AB builds an expense app for its pilot customer Bergström & Co, with nine storylines (receipt reader, VAT
requirement, offline queue, Fortnox outage, duplicate payout, onboarding, approval limits, the 1.0 release, image
retention). After all layers the graph has 542 nodes and 2 851 relationships; nothing is stale.

## Pipeline

| # | Layer (tab) | Code | Kind |
| --- | --- | --- | --- |
| 0 | Import (SQL viewer) | `viewer/app.py`, `viewer/person_identity.py` | deterministic |
| 1 | Reference extraction | `backend/reference_extraction.py` | regex |
| 2 | Knowledge layer | `backend/topic_event_extraction.py` | LLM (`gpt-5.6-terra`), 1 call per issue |
| 3 | Architecture layer | `backend/architecture_layer.py` | deterministic + LLM, 1 call per repository |
| 4 | Root cause & impact layer | `backend/causal_layer.py` | LLM, 1 call per topic |
| 5 | Expertise & collaboration layer | `backend/collaboration_layer.py` | deterministic |
| 6 | Graph algorithms | `backend/graph_algorithms.py` | networkx |
| 7 | Embeddings | `backend/embedding_pass.py` | OpenAI embeddings |

Staleness: `backend/pipeline_staleness.py`. After rebuilding any layer, rebuild every layer after it
(`docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 5).

## The AI agent

Plan-and-execute with parallel layer specialists (`sources`, `causes`, `architecture`, `people`), a code `check`, an
optional explorer and one streamed answer (`docs/AI_AGENT_HANDOFF.md`). Models: `gpt-6-sol` (planner, explorer,
answer at reasoning effort `medium`), `gpt-6-luna` (specialists, summary). The specialists add compact facts (the
sources of a topic, the causal chain to an event, the people outside the team, the groups), read the matching part of
long documents, and follow a pull request that a message names. All 27 test questions pass (q01-q10 are the demo
questions in `kvitta/demofrågor.md`, q19-q27 control questions never used for tuning); a full run costs about $0.40.
The chat survives a page reload (sessionStorage); the conversation itself lives in backend memory.

Decisions: no loop from `check` back to the planner; four specialists grouped by kind of question; the evidence is
limited to 8 nodes per specialist, and facts carry complete lists instead of more nodes.

## Microsoft GraphRAG

A second RAG system on the same sources, Microsoft's own library (`docs/GRAPHRAG_HANDOFF.md`): the sources are
exported as 70 documents, `graphrag index` builds its own graph (829 entities, 2 199 relationships), hierarchical
Leiden communities (225 in 4 levels) with a community report each, and embeddings; Microsoft's global, local, DRIFT
and basic search answer from it. It runs in its own Python environment as a service the backend calls, never touches
Neo4j, and is used from the `Microsoft GraphRAG` tab, the chat (`MS GraphRAG` button) and the graph panel (`MS Graph`
button). Indexing uses `gpt-5.6-terra` (a full build cost 8.85 USD), searching `gpt-6-luna` (0.0006-0.033 USD per
question, limit 0.10 USD). On the agent's 27 test questions: local, global and DRIFT 17, basic 15, the agent 27; on the
18 questions the source texts can answer, local 16.

## Done on 2026-09-29 and 2026-09-30

- The Kvitta scenario written (`kvitta/`), its data built (`data/kvitta_seed.sql`), the old data backed up in
  `backups/old-data-auth17-2026-09-29/`, the graph imported and all seven layers built and verified against the data.
- Knowledge and Root cause & impact layers: causal links in time order, cross-topic links deduplicated per pair,
  sharper instructions for root causes and cross-topic links.
- The agent: chunk texts in the evidence, the facts listed above, the reserved place for a named pull request, neutral
  prompt examples, answer effort `medium`, the new test questions.
- The graph panel: the graph API without embedding vectors (2.2 MB instead of 13.5 MB), the layout in steps with a
  progress line, the Distance slider applied on release, the Knowledge view fitting the panel, fixed colours for
  topics, events and chunks, motion on from the start, fact citations behind `Show facts`.
- Every document in `docs/`, `README.md` and `AGENTS.md` updated to the Kvitta data.

## Done since then (2026-09-30 to 2026-10-08)

- The app deployed to a Google Cloud VM with Docker Compose (`DEPLOY_HANDOFF.md`).
- Microsoft GraphRAG, step by step on the branch `microsoft-graphrag`: its environment and tab, the input export, the
  service, the configuration (`graphrag init`, `prompt-tune`, entity types), the index, the views of the graph and
  the communities, the four searches with cost counting and a cost limit, the chat mode, the graph window, the
  evaluation, the server container, and the documentation.

## Where to continue

1. Merge `microsoft-graphrag` into `main` when the user wants (tag the current `main`, for example `before-graphrag`,
   first). The server already runs the branch; merging does not change it.
2. At hand-in: allow `data/kvitta_seed.sql` in `.gitignore` so the teacher can load the example data.
