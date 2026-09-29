# Session Handoff (2026-09-29)

Handoff for the next Claude session. Moved from the repository root to `docs/` on 2026-09-29 and checked against the
code and both databases that day.

**Current topic: new example data.** Another model (Claude) will generate a larger example dataset so that every
graph layer and the agent can be tested properly. On 2026-09-29 every document in `docs/` was rewritten from the code
and the live databases for that purpose. Start with `docs/DATA_GENERATION_GUIDE.md` and `docs/SQL_DATA_HANDOFF.md`.

## Documents

| Document | Content |
| --- | --- |
| `AGENTS.md` (root) | Project rules: venv, scripts, database access, language |
| `docs/DATA_GENERATION_GUIDE.md` | What a new dataset must contain per layer; validation queries |
| `docs/SQL_DATA_HANDOFF.md` | The six sources and ten tables: every column, constraint, JSON shape, identity and reference rule, import behaviour, DDL |
| `docs/GRAPH_DATA_HANDOFF.md` | Every node label, relationship type, constraint and index in Neo4j; graph API and panel |
| `docs/PIPELINE_AND_LINKS_HANDOFF.md` | Build order, prerequisites, staleness, what rebuilds remove, paths through the graph, evidence identifiers |
| `docs/REFERENCE_EXTRACTION_LAYER_HANDOFF.md` ... `docs/EMBEDDING_LAYER_HANDOFF.md` | One per layer (References, Knowledge, Architecture, Root cause & impact (`CAUSAL_`), Expertise & collaboration (`COLLABORATION_`), Graph algorithms, Embeddings) |
| `docs/AI_AGENT_HANDOFF.md` | The chat agent: flow, nodes, settings, test questions, open issues |
| `docs/SCALING_HANDOFF.md` | What must change before much larger data (not scheduled) |

The file names `SQL_DATA_HANDOFF.md`, `EMBEDDING_LAYER_HANDOFF.md` and `AI_AGENT_HANDOFF.md` are referenced from code
comments (`viewer/person_identity.py`, `backend/embedding_pass.py`, `backend/ai_agent/__init__.py`); keep them.

## Working with this user

- Talk Swedish. Write code, comments, docs and commit messages in English (`AGENTS.md`).
- **Never install anything without asking twice.** Global rule, no exceptions.
- **Confirm before any change**: state the plan and wait for an explicit "ja". "Vad tycker du" / "visa först" /
  "koda inte" means propose with a recommendation and wait. "Kör" means implement. Build exactly what was agreed.
  Correcting a statement in the docs that one of our changes made untrue may be done without asking (say so after).
- **Commits:** the user (Hassan Mehdi) is the only author; no `Co-Authored-By` line; English message written to a
  scratchpad file and committed with `git commit -F <file>` (PowerShell 5.1 splits inline messages with quotes).
  Commit only when asked, push only when asked ("committa" alone means commit only). Check that `git add` succeeded and
  what is staged before committing.
- The user is careful about the **build code** (anything that writes to Neo4j) and the **graph panel** (left box):
  prefer read-only changes; for write paths explain the risk and ask first; keep graph panel changes minimal and
  isolated.
- Mark undecided proposals as such in the docs ("not decided, proposal only").
- Keep data-specific names out of fixed UI captions.
- Verify after every change: `frontend\node_modules\.bin\tsc.cmd --noEmit -p .` (from `frontend`),
  `.\.venv\Scripts\python.exe -m py_compile <file>`, and hit the API. Say plainly what was not checked in the browser.
- Anything that calls OpenAI costs money: layer builds (they also write to Neo4j: ask first), agent test runs (fine
  when the user agreed to the work; report the cost).
- When a change touches backend and frontend, restart the backend before the frontend part is saved (Vite reloads at
  once); after a backend restart, tell the user to press F5.

## Running the app

- **Neo4j** runs without the Neo4j Desktop app: `scripts\run_neo4j.ps1` starts the database Desktop installed, hidden
  in the background (Bolt 7687, HTTP 7474; Desktop's Java 21, `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`); it refuses to
  start when that database or anything on 7687 already runs. `scripts\stop_neo4j.ps1` stops it (close request, forced
  after 30 s). Log: `logs\neo4j.log` in the DBMS folder.
- **Backend**: `scripts\run_backend.ps1` (Flask, port 8000). Two `python.exe` per backend is normal: the venv's
  `python.exe` is a launcher that starts the real interpreter. The auto-reloader is off (`BACKEND_RELOAD=1` in `.env`
  turns it on). After editing backend code: find the listener on 8000 (`Get-NetTCPConnection -State Listen -LocalPort
  8000`), stop the tree from the launcher (`taskkill /PID <launcher> /T /F`), start the script again. Prompts and
  module constants are read at start; `backend/ai_agent/settings.json` is read per question.
- **Frontend**: `npm.cmd run dev -- --host 127.0.0.1` in `frontend` (Vite dev server on 5173, proxies `/api` to 8000).
  `scripts\run_app.ps1` starts backend and Vite together. Locally only the dev server is used; a built frontend is for
  the server deployment (served by Nginx).
- **SQL viewer** (port 5000, the import buttons): started from the app's `Open SQL Viewer` button or
  `scripts\run_viewer.ps1`.
- **PostgreSQL 18** runs as a Windows service (port 5432). Read-only access: `C:\Program Files\PostgreSQL\18\bin\psql.exe`
  with values from `.env` at runtime and `PGOPTIONS=-c default_transaction_read_only=on`. Never print `.env`.
- Read-only graph inspection: scratchpad scripts reusing `neo4j_config` from `scripts/inspect_neo4j.py`, sessions with
  `default_access_mode="READ"`, run with `.\.venv\Scripts\python.exe`.
- Installed (2026-09-29): Neo4j 2026.08.1 Enterprise (Cypher `SEARCH`, scoped `CALL (x) { ... }`), PostgreSQL 18.6,
  langgraph 1.2.11, openai 3.16.2, neo4j driver 6.3.1, networkx 3.7, Flask 3.1.3, psycopg 3.3.5, pydantic 2.13.5.

## The project in one paragraph

Simulated source material from a software team (mail, Slack, Teams transcripts, issues, documents, PRs; ten SQL tables
in PostgreSQL, the source of truth) is imported into Neo4j and enriched by seven layers into a memory graph; an AI
agent answers questions over it. Today's data is one story (64 SQL rows, 108 graph nodes, 488 relationships):
administrator sessions expiring too early (`AUTH-17`), a first fix rejected by security, a web-only fix, a mobile
regression (`AUTH-19`). Every layer is built and up to date (no stale warnings on 2026-09-29).

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

## The AI agent (state on 2026-09-29)

Plan-and-execute with parallel layer specialists (`sources`, `causes`, `architecture`, `people`), a code `check`, an
optional explorer and one streamed answer (`docs/AI_AGENT_HANDOFF.md`). Models since 2026-09-27: `gpt-6-sol` (planner,
explorer, answer), `gpt-6-luna` (specialists, summary); 14 of 14 test questions passed in six runs on 2026-09-28 at
about $0.11 per run. The chat survives a page reload (sessionStorage) but a backend restart forgets the conversation.
The test questions are tied to today's data and must be rewritten after new data is loaded.

Decisions: no loop from `check` back to the planner; running without data is not a case to optimise; four specialists
grouped by kind of question. Open, not decided: off-topic questions run every specialist; `check` cannot tell whether
the evidence answers the question (`docs/AI_AGENT_HANDOFF.md`, section 10).

## Done on 2026-09-29

- Neo4j runs without Neo4j Desktop (`run_neo4j.ps1`, `stop_neo4j.ps1`); the graph turns as one picture (`Motion`);
  the right panel lights up while the AI works; typing pace reworked; centre tabs restyled as pills (see git log).
- All of `docs/` rewritten from the code and the live databases: new `DATA_GENERATION_GUIDE.md` and
  `PIPELINE_AND_LINKS_HANDOFF.md`; every layer document, the SQL, graph and agent documents rewritten;
  `SCALING_HANDOFF.md` checked; `BESLUTAD_PROJEKTINRIKTNING.md` removed (its goals are in `README.md` and the data
  guide); `README.md` updated; this file moved here.
- Found while checking and now documented (code unchanged): the Knowledge build answers 500, not 503, without an API
  key; the importer writes no `DocumentVersion` nodes for a document whose latest version has no resolvable author;
  reply edges for issue comments and PR reviews are only written when the parent's ID sorts before the reply's; the
  import never deletes nodes, so a replaced dataset needs an emptied graph.

## Where to continue

1. Generate the new example data with the other model (user's decision whether it replaces or extends today's).
2. The user loads it, empties the graph, imports and rebuilds the layers (costs a little; the user runs or approves
   it).
3. Rewrite `backend/ai_agent/test_questions.json` from the new graph and run the test set.
4. Deployment to a server is planned separately with the user (Docker Compose, Nginx serving the built frontend).
