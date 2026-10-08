# Microsoft GraphRAG Handoff

The app has two RAG systems side by side:

- **The app's own system**: the seven graph layers in Neo4j and the LangGraph agent (`docs/AI_AGENT_HANDOFF.md`).
- **Microsoft GraphRAG** (in the app: **MS GraphRAG**): Microsoft's own library `graphrag` 3.2.0, run on the same
  source data. It builds its own graph from the source texts, groups it with hierarchical Leiden communities, writes a
  community report per community, and answers with Microsoft's four searches (global, local, DRIFT, basic).

The two share only the source data in PostgreSQL. Microsoft GraphRAG never reads or writes Neo4j, and the app's layers
and agent never use GraphRAG's files. The rest of the app works when the GraphRAG service is not running.

**Verified on 2026-10-08** against the code on the branch `microsoft-graphrag`, the local index and the server.

## 1. The pipeline

```text
PostgreSQL --> Input (Import): 70 documents --> Index (Build) --> Query (Search), Chat with AI, Evaluation
```

The index step, in the user's own words:

```text
Config --> Documents --> Text units --> Graph --> Communities --> Community reports --> Embeddings
```

Config, documents and text units are the preparation; what the index creates is the **graph** (entities and
relationships), the **communities**, the **community reports** and the **embeddings**.

| Step | What happens | Model |
| --- | --- | --- |
| Input (Import) | The six sources are read from PostgreSQL (read only) and written as 70 documents to `graphrag_project/input/documents.jsonl` | none |
| Text units | Every document is split into text units of 1 200 tokens (100 overlap), with the document title first in each: 76 text units | none |
| Graph | The model reads every text unit and extracts entities (nodes) and relationships (edges), with one extra pass ("gleaning") for missed entities; descriptions of the same entity are summarised into one | `gpt-5.6-terra`, reasoning `medium` |
| Communities | Hierarchical Leiden on the graph: a community with more than 10 entities is split again, which gives levels from broad (level 0) to detailed | none |
| Community reports | The model writes a report per community: title, summary, rating 0-10 with an explanation, findings | `gpt-5.6-terra` |
| Embeddings | Entity descriptions, community reports and text units are embedded and stored in LanceDB | `text-embedding-3-large` |

## 2. What the index holds (Kvitta data, built 2026-10-07)

| Part | Count |
| --- | --- |
| Documents | 70 (7 mail threads, 21 Slack threads, 10 Teams meetings, 12 issues, 6 documents, 14 pull requests) |
| Text units | 76 |
| Entities (nodes) | 829, in 11 types |
| Relationships (edges) | 2 199 |
| Communities | 225 in 4 levels: 15 (level 0), 69, 125, 16 |
| Community reports | 225, one per community |
| Embeddings | 829 entity descriptions, 225 community reports, 76 text units |

The 15 level 0 communities match the Kvitta storylines (the Fortnox outage, the duplicate payout and the offline queue,
the receipt reader and VAT, approval limits, image retention, the 1.0 release and others): GraphRAG found them on its
own.

Storage: no database server. The graph and the reports are **Parquet** files (`entities.parquet`,
`relationships.parquet`, `communities.parquet`, `community_reports.parquet`, `text_units.parquet`,
`documents.parquet`), read into memory with pandas; the vectors are in **LanceDB** (`output/lancedb/`, three tables:
`entity_description`, `community_full_content`, `text_unit_text`).

## 3. Parts and files

| Part | File | Runs in |
| --- | --- | --- |
| Input export | `backend/graphrag_input.py` | the backend (`.venv`, Python 3.14): it reads PostgreSQL |
| API routes | `backend/graphrag_routes.py` (a Flask blueprint; `app.py` only registers it) | the backend |
| Evaluation | `backend/graphrag_evaluation.py` | the backend |
| GraphRAG service | `graphrag_service/server.py` (standard library HTTP server, 127.0.0.1:8100) | `.venv-graphrag` (Python 3.13) |
| GraphRAG project | `graphrag_project/settings.yaml`, `prompts/`, `prompts_tuned/`; `input/`, `output/`, `cache/`, `logs/` (not in git) | read by the service |
| Tabs | `frontend/src/MicrosoftGraphRagPanel.tsx` | the browser |

**Why a separate service.** `graphrag` 3.2.0 is built for Python 3.11-3.13 and the backend runs Python 3.14, so
GraphRAG gets an environment of its own. The service runs in it (`.venv-graphrag`, installed with
`scripts\install_graphrag_deps.ps1` from `requirements-graphrag.txt`) and the backend calls it. It answers with
Microsoft's own code: `graphrag index` for the build and the functions `graphrag query` runs for the searches.

**Starting it.** `scripts\run_app.ps1` starts the service, the backend and the frontend (and stops them together);
`scripts\run_graphrag_service.ps1` starts the service on its own. At start the service warms up in the background
(Microsoft's search code, the settings, the index tables, the tokenizer; no model call), so the first question does
not wait for it. After editing `graphrag_service/server.py`, restart the service.

## 4. The tabs

The **Microsoft GraphRAG** tab sits at the right end of the top tab row. Every inner tab shows whether the service runs.

| Inner tab | Shows | Does |
| --- | --- | --- |
| **Input (Import)** | The 70 documents (source, title, records, period, characters); a click shows the text GraphRAG reads | `Export input documents`: reads PostgreSQL (read only) and writes the file only when the data changed (a SHA-256 of the content); one export at a time |
| **Index (Build)** | The chain Graph -> Communities -> Community reports -> Embeddings with an explanation; the configuration (from GraphRAG's own loader); the last build, what it created, communities per level, time per step | `Build index`: runs `graphrag index`. Locked (`Index is up to date`) when neither the input nor any setting or prompt that indexing reads has changed; a build asks first and shows the measured cost of a full build (8.85 USD) |
| **Entities & relationships** | The entities (nodes), most connected first, with search and a type filter; the relationships with their start and end node and weight; 50 rows at a time, long descriptions open on a click | `Show graph`: the graph window (section 7) |
| **Communities & community reports** | The communities as a tree from level 0 down; each row shows the title, the five most connected entities with their types, level, size, rating and sub-communities; a click opens the community report and the sub-communities | |
| **Query (Search)** | The method (Global, Local, DRIFT, Basic) with an estimated cost, the question, the answer with its cost, and the context the model was given (Entities (nodes), Relationships (edges), Community reports, Text units (original text)) | `Ask` |
| **Evaluation (Compare)** | The app's 27 test questions answered by each search and by the app's own agent (section 9) | `Run evaluation` with the chosen methods |

## 5. Configuration (`graphrag_project/settings.yaml`)

`graphrag init` wrote the file and the prompts; every change from Microsoft's defaults is explained in a comment in
the file.

| Setting | Value | Why |
| --- | --- | --- |
| Indexing model | `gpt-5.6-terra`, reasoning effort `medium` | The same model and effort as the app's Knowledge layer, so the two graphs differ by method, not by settings |
| Search model | `gpt-6-luna` (`search_completion_model`) | Searches send many report and text unit tokens; with the indexing model a global search cost 0.67 USD and a DRIFT search about 2 USD |
| Embedding model | `text-embedding-3-large` | The same as the app's own embeddings |
| Input | `jsonl`, columns `id`, `title`, `text` | The export file |
| Text units | 1 200 tokens, overlap 100, `prepend_metadata: [title]` | The title gives the later units of a long document their context |
| Entity types | person, organization, software component, external integration, issue, pull request, release, requirement, document, meeting, business concept | The 25 types `graphrag prompt-tune` found in the data, with overlapping types merged (as its own instruction asks); professional role and communication channel left out |
| Extraction prompt | Microsoft's original | `prompt-tune`'s version is not used: in 3.2.0 its examples pair each text with the output for another text (one message builder is reused for every example) |
| Description summary prompt | Microsoft's original | `prompt-tune`'s version drops the length limit and asks to "enrich" from text the model is not given |
| Community report prompt | `prompts_tuned/community_report_graph.txt` | `prompt-tune`'s persona and a rating scale for this domain, with Microsoft's length line put back |
| Claims | off | Microsoft's default |
| Leiden | `max_cluster_size: 10` | Microsoft's default |
| DRIFT | `primer_folds: 2`, `n_depth: 1`, `drift_k_followups: 3`, both temperatures 1 | Fewer rounds than the defaults (5, 3, 20; about 50 calls per question); `gpt-6-luna` accepts only temperature 1 |
| API key | `${OPENAI_API_KEY}` | From the project's `.env`, loaded by the service; no second file with secrets |

`prompt-tune` ran once (11 calls, 0.31 USD); its output is kept in `prompts_tuned/`.

## 6. The searches

| Method | Finds its context by | Model calls | Measured cost | Time |
| --- | --- | --- | --- | --- |
| **Local** | Vector search of the question against the entity descriptions: the 10 closest entities, then their relationships, the reports of their communities and the text units that name them, within 12 000 tokens (50 % text units, 15 % reports) | 1 | 0.0013 USD | about 6-18 s |
| **Global** | No search: reads every community report at level 2 in batches of about 12 000 tokens (map) and merges the answers (reduce) | 22 | 0.033 USD | about 16-32 s |
| **DRIFT** | Starts from the community reports like global, then one round of up to 3 local follow-ups | about 7 | 0.008 USD | about 43-60 s |
| **Basic** | Vector search against the text units; no graph | 1 | 0.0006 USD | about 4-6 s |

GraphRAG finds its context by vector search in LanceDB; local search takes the entities it finds together with what
lies directly around them in the graph. The answers cite their context as
`[Data: Entities (2, 3); Relationships (23); Sources (43); Reports (14)]`: the numbers are the ids of the rows in the
tables under "What GraphRAG used" (Sources are the text units).

The defaults of `graphrag query` are used: community level 2, an answer in multiple paragraphs. Every question is
answered on its own.

## 7. Cost protection and errors

- **Cost per question.** The service counts every model and embedding call made for a question (it wraps
  `litellm.acompletion`, `litellm.aembedding` and `litellm.embedding`, which `graphrag_llm` looks up at every call) and
  prices it with litellm's price list. Streamed answers ask for their token counts (`include_usage`). The cost is shown
  under every answer; counting never makes a question fail.
- **Limit: 0.10 USD per question.** Before every call its input is priced (counted locally with the tokenizer) and
  reserved; a call that would pass the limit is refused and the question is reported as stopped, with what it cost.
  The refusal is raised as `BudgetExceededError`, a name on graphrag's list of errors it never retries.
- **Empty OpenAI account.** OpenAI reports it as a rate limit error, which graphrag would retry seven times with waits
  of up to 128 seconds; the service turns it into an immediate stop with the message "OpenAI: no credits remaining".
  Ordinary rate limits are still retried.
- **One question at a time** in the service, so the cost of one question is never mixed with another's.

## 8. Index state and the build button

`output/app_index_run.json` records what the index was built from: the SHA-256 of the input file and a fingerprint of
exactly what indexing reads, taken from GraphRAG's own loaded settings (input, chunking, extraction, summaries,
claims, Leiden, community reports, embeddings, the models those steps use without the key, and their prompt files).
Search settings are not part of it, so changing the search model never makes the index stale. The prompts are read with
CRLF as LF (fingerprint version 2), so the same files give the same fingerprint on Windows and on the server; a
version 1 record is moved to version 2 when it still matches.

The index on hand was built from the command line on 2026-10-07 (before the button existed) and recorded once by the
service ("built outside the app"). A build from the app writes the record before it starts (`running`) and after it
ends (`finished` or `failed`), so a build stopped half-way is known.

The full build took 14 minutes (graph extraction 200 s, community reports 299 s, embeddings 344 s) and cost 8.85 USD:
825 model calls (1 160 857 tokens in, 541 072 out) and 92 embedding calls. Model answers are cached in
`graphrag_project/cache/`: a build with unchanged input and settings costs almost nothing; a changed extraction prompt
or entity types make the extraction run again (about the full cost).

## 9. Evaluation (2026-10-08)

The app's own 27 test questions (`backend/ai_agent/test_questions.json`), unchanged, scored in code with the agent's
own rule for terms: every group of expected terms ("Answer must include") must have a variant in the answer. The
agent's other checks (route, evidence nodes, facts) name nodes of its own graph and cannot be applied to GraphRAG. The
agent is not run again: its latest run (2026-09-29) is scored with the same rule. Results are kept in
`backend/graphrag_evaluation_last.json` (not in git).

| System | All 27 | Fair (18) | Need the app's own graph (6) | Need record ids (3) | Control q19-q27 (9) | Cost | Average time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| App's own agent | 27 | 18 | 6 | 3 | 9 | 0.41 USD | 12.9 s |
| MS GraphRAG, Local | 17 | 16 | 0 | 1 | 7 | 0.034 USD | 6.4 s |
| MS GraphRAG, Global | 17 | 15 | 1 | 1 | 7 | 0.81 USD | 16.2 s |
| MS GraphRAG, DRIFT | 17 | 15 | 1 | 1 | 7 | 0.22 USD | 58.9 s |
| MS GraphRAG, Basic | 15 | 14 | 0 | 1 | 6 | 0.019 USD | 3.9 s |

- **Need the app's own graph:** q06 (bus factor), q07 (betweenness, weighted degree), q12 and q23 (the groups of
  people), q13 (betweenness), q14 (component dependencies). These values exist only in the app's layers.
- **Need record ids:** q04 (`mail-006`/`mail-007`), q19 (`mail-010`), q26 (`upload.ts`).
- **Fair:** the other 18; the answer is in the source texts.
- The agent was tuned on q01-q17; the control questions q19-q27 never were, which makes them the fairest comparison.
- The terms rule is strict: in q01 every GraphRAG search describes the mechanism (lost connection, timeout, retry,
  an export without a duplicate check) without the word "offline"; in q05 three searches name Bergström & Co but not
  Anders. The tab shows every answer side by side.

What it shows: on questions the source texts can answer, GraphRAG's local search comes close to the agent (16 of 18)
at a fraction of the cost per question; the graph helps (local and global/DRIFT beat basic, which has no graph);
questions that need the app's own layers are missed by every search.

## 10. In the rest of the app

- **Chat with AI:** an `MS GraphRAG` button below `New` lets Microsoft GraphRAG answer instead of the agent (filled
  blue while on), with the search method beside it. A switch starts a new chat. The answer shows the search, the time
  and the cost; the `[ ]` button shows or hides its `[Data: ...]` references like the agent's.
- **Graph panel:** an `MS Graph` button in its `Window` group opens the graph window.
- **Graph window** (also `Show graph` in Entities & relationships): the 150 most connected entities or all 829, one
  colour per level 0 community with a list that shows one community at a time, a click on a node lights it and its
  neighbours; the beige grid of the app's graph. The node positions are computed once by the service (networkx spring
  layout per level 0 community, clusters side by side, largest first) and kept in `output/app_graph_layout.json`, so
  the window draws at once without a force layout.

## 11. API

Backend routes (`backend/graphrag_routes.py`); all but the input and evaluation routes pass the request on to the
service. GET is open on the server; every other method needs the login (Nginx).

| Route | Does |
| --- | --- |
| `GET /api/graphrag/status` | Whether the service runs; graphrag and Python versions |
| `GET /api/graphrag/config` | `settings.yaml` loaded and validated by GraphRAG, summarised (never the key) |
| `GET /api/graphrag/input` / `POST /api/graphrag/input/export` | The input documents / export them |
| `GET /api/graphrag/index` / `POST /api/graphrag/index` | Index state, run progress, why a build is needed / start a build (409 when up to date or running) |
| `GET /api/graphrag/entities` | Every entity and relationship |
| `GET /api/graphrag/communities`, `GET /api/graphrag/communities/<id>` | The community tree / one report with its findings and entities |
| `GET /api/graphrag/graph` | Entities with fixed positions and level 0 community, relationships |
| `POST /api/graphrag/query` | `{"method", "question"}`: the answer, the context tables, the time and the cost (402 when stopped by the limit or an empty account) |
| `GET /api/graphrag/evaluation` / `POST /api/graphrag/evaluation` | Questions and results / run the chosen methods |

## 12. On the server

The `graphrag` container (`graphrag_service/Dockerfile`, Python 3.13 with `requirements-graphrag.txt`) has no published
port; the backend reaches it at `http://graphrag:8100`. `graphrag_project/` is mounted into it and into the backend, so
the index survives a rebuild. The input and the index are built locally and copied to the server with `tar` (which
keeps the file times); the code is copied with `git -c core.autocrlf=false archive`, so the server gets LF line
endings. Details: `deploy/README.md` and `DEPLOY_HANDOFF.md`.
