# Handoff: Working on This Repository

This file is for an AI assistant taking over this project.

Claude Code reads `AGENTS.md` automatically at the start of every session. Other assistants do not. Everything that file would have given you is repeated here, together with the project history that explains why the code resists certain changes.

Read this before touching anything. Every number in section 6 was measured against the live databases, not copied from a report.

---

## 1. What this project is

Simulated source material from a software engineering team, modelled in two layers.

**PostgreSQL is the source-preserving layer.** Ten tables holding original records with stable IDs, timestamps, versions and source references. It is the source of truth.

**Neo4j is the relationship and memory layer.** Derived entirely from the SQL tables and rebuildable from them at any time. Its purpose is to find connections between people, needs, discussions, decisions, issues, documentation and implementation.

The eventual goal is a GraphRAG memory system that can answer causal questions — why something took thirteen days, what blocked it, who decided what — by traversing evidence rather than guessing.

---

## 2. Read these first, in this order

| Document | Why |
| --- | --- |
| The task document you were given | Tasks are written to stand on their own: labels, keys, properties, relationship types, exact expected counts. |
| `docs/GRAPH_DATA_HANDOFF.md` | The complete graph model. The single most important reference. |
| `docs/SQL_DATA_HANDOFF.md` | The PostgreSQL schema, table by table, plus data generation rules. |
| `TASK_01_PERSON_IDENTITY_REPORT.md` | Person identity resolution. Essential before touching anything involving `Person`. |
| `TASK_02_ISSUE_VERSIONING_REPORT.md` | Why there are three issue tables. |
| `TASK_03_RETRIEVAL_UNITS_REPORT.md` | Why embedded JSON became first-class nodes. |
| `TASK_04_CREATED_ISSUE_REPORT.md` | Short. Creator versus assignee. |
| `TASK_05_REFERENCE_EXTRACTION_REPORT.md` | The derived reference layer and how it is kept re-runnable. |

The task document tells you *what* to build. The reports tell you *why the surrounding code is shaped the way it is*.

---

## 3. Project conventions

Binding. From `AGENTS.md`.

**Python.** Always the project virtual environment, through the project scripts. Never bare `python` or `pip`, never global or user Python.

```powershell
.\scripts\install_deps.ps1      # dependencies
.\scripts\run_viewer.ps1        # SQL viewer, port 5000
.\scripts\run_app.ps1           # backend 8000 + React frontend 5173
```

The interpreter is `.\.venv\Scripts\python.exe`.

**Database.** Do not modify either database unless the user explicitly asks. Read-only checks are fine when requested. Connection values come from `.env` at runtime and are never committed. Local PostgreSQL client:

```
C:\Program Files\PostgreSQL\18\bin\psql.exe
```

Ask for elevated permission before starting or stopping the PostgreSQL service.

**Language.** Code, comments, commit messages and project documentation in English. Talk to the user in Swedish unless they ask otherwise.

**Data model.** Six logical data sources. Ten tables, because some sources need several. Never flatten them into one generic event table. The SQL viewer is source-oriented, not table-oriented: one source view can combine several tables.

### Two files are stale

- **`AGENTS.md`** says the schema has nine tables and lists them without `issues`. It is **ten** since Task 02.
- **`README.md`** repeats the same error.

Both were outside the scope of every task since, so they were reported rather than changed. Everything else in `AGENTS.md` is accurate and binding.

The correct source groups:

| Logical source | Tables |
| --- | --- |
| Mail | `mail_messages` |
| Slack / project chat | `slack_messages` |
| Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` |
| Issues / tickets | `issues`, `issue_versions`, `issue_comments` |
| Requirements and technical documentation | `document_versions` |
| Pull requests, code reviews, code changes | `pr_versions`, `pr_reviews` |

`issues` is **not** a seventh source. It belongs to the Issues source exactly as `teams_transcript_segments` belongs to Teams, and must not appear as its own frontend filter or viewer menu entry.

---

## 4. What has been built

### Task 01 — Person identity resolution

`Person` nodes were keyed from whatever field a source happened to provide, so one real person split into several nodes.

Only mail, Slack and Teams participants carry an email address. Issues, documents, pull requests and transcript speakers carry only a source ID and a name. Identity therefore cannot be resolved one source at a time. `viewer/person_identity.py` collects observations from every person-bearing field, then clusters them with union-find: email is the strongest signal, source ID second, and both share one structure so a row carrying **both** bridges the two halves. A name alone may only attach a nameless observation to exactly one existing cluster, and never merges two established identities.

`Person` carries `emails`, `source_ids`, `names`, `identity_confidence` and `identity_ambiguous`. Task 03 added `actor_type`.

### Task 02 — Issue versioning

`issue_versions` was built for history but a unique constraint allowed one row per issue, and `issue_comments` referenced that constraint. Identity moved to a new `issues` table; `issue_versions` keeps state only and can now hold many rows per issue. `scripts/migrate_issue_versioning.py` performs the migration in one transaction and is idempotent.

### Task 03 — Retrieval units

Content embedded as JSON strings became first-class nodes: `IssueVersion`, `IssueComment`, `DocumentVersion`, `PullRequestReview`, `CodeChange`. A relationship can point at a node; it cannot point at part of a property. The raw JSON properties were deliberately kept alongside them.

Also added `actor_type` on `Person`, distinguishing a human from a functional mailbox by exact local-part match.

### Task 04 — `CREATED_ISSUE`

The issue creator had no edge at all. `CREATED_ISSUE` now comes from `issues.creator_source_id`, while `OWNS_ISSUE` still follows the assignee. Two different roles, two relationships.

### Task 05 — Derived reference layer

The six source trees shared only `Person`, so the only path from a mail to a pull request ran through a human being. But the content connections are written down in the text: `AUTH-17` appears in Slack, in a transcript, in two document versions.

`backend/reference_extraction.py` turns those strings into `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST` and `MENTIONS_DOCUMENT` edges. Entirely mechanical: regular expressions and lookups built from the graph itself. It runs from the **Build graph layers** tab in the frontend, never as part of an import.

These edges carry `derived: true`. Everything else in the graph is a transcript of a SQL row; these are the first thing the system concluded rather than copied.

---

## 5. Why the code resists certain changes

Each of these looks like something worth tidying up if you do not know the history.

**`person_key` is never built by string concatenation.** Every import path calls `resolve_person_key(email, source_id, name)`. If you find yourself writing `f"email:{...}"` in the import path, you have reintroduced the bug Task 01 fixed.

**Relationship creation uses `MATCH (p:Person {person_key: ...})`, not `MERGE`.** `Person` nodes are written centrally from the resolved clusters, with all their properties, before any relationship is created. A `MERGE` there would let one source row create a bare node or overwrite resolved properties with narrower values. The `MATCH` is load-bearing.

**A new person-outgoing relationship type must be added to `PERSON_OUTGOING_RELATIONSHIPS` in `viewer/app.py`.** That list is what `move_person_relationships` walks when a superseded `Person` node is merged into its canonical node. A type missing from it is left orphaned when a person key changes.

**Relationship type names are source-filter-specific.** `backend/app.py` maps frontend filters by relationship type, so a type shared between two source groups would put nodes under the wrong filter. Hence `HAS_ISSUE_VERSION` and `HAS_DOCUMENT_VERSION`, never a generic `HAS_VERSION`.

**Name alone never merges two identities.** An ambiguous name produces its own node with `identity_confidence: "weak"` and `identity_ambiguous: true`. That is the design, not a defect to resolve by picking the likeliest match.

**Issue identity and issue state are separate tables on purpose.** Do not reintroduce a constraint that allows one version row per issue.

**Raw JSON properties are kept alongside the retrieval-unit nodes.** Removing them would be an unrelated behaviour change.

**The reference pass deletes by `extracted_by`, never by relationship type.** That is what keeps it from touching an import-created relationship.

**Optional backend features must be imported inside their endpoint.** A module-level import of the chat agent once took the entire graph API down because one dependency was missing. See section 8.

---

## 6. Current verified state

Measured directly, not quoted.

**PostgreSQL: 54 rows across ten tables** (seed dataset 01, "Administrator session timeout").

| Table | Rows | | Table | Rows |
| --- | --- | --- | --- | --- |
| `mail_messages` | 4 | | `issue_comments` | 5 |
| `slack_messages` | 12 | | `document_versions` | 3 |
| `teams_meetings` | 2 | | `pr_versions` | 4 |
| `teams_transcript_segments` | 9 | | `pr_reviews` | 6 |
| `issues` | 2 | | `issue_versions` | 7 |

**Neo4j: 68 graph nodes, plus one `PipelineState` bookkeeping node. 180 relationships. 13 constraints.**

| Label | Nodes | | Label | Nodes |
| --- | --- | --- | --- | --- |
| `Person` | 7 | | `IssueComment` | 5 |
| `MailMessage` | 4 | | `Document` | 2 |
| `SlackMessage` | 12 | | `DocumentVersion` | 3 |
| `TeamsMeeting` | 2 | | `PullRequest` | 2 |
| `TeamsTranscriptSegment` | 9 | | `PullRequestReview` | 6 |
| `Issue` | 2 | | `CodeChange` | 7 |
| `IssueVersion` | 7 | | | |

**125 imported relationships + 55 derived = 180.**

The derived ones: `MENTIONS_ISSUE` 22, `MENTIONS_PULL_REQUEST` 21, `MENTIONS_DOCUMENT` 12.

**The seven `Person` nodes**, three of which are deliberate test cases:

| `person_key` | `actor_type` | Confidence | Note |
| --- | --- | --- | --- |
| `email:anna.lindqvist@example.com` | person | strong | bridged to `u-anna` |
| `email:anna.berg@example.com` | person | strong | bridged to `u-annab` |
| `email:erik.nilsson@example.com` | person | strong | bridged to `u-erik` |
| `email:martin.ek@northwind.example.com` | person | strong | external customer, no source ID |
| `email:support@example.com` | **mailbox** | strong | shared mailbox, not a human |
| `source:u-priya` | person | strong | no email anywhere; must never join an email cluster |
| `name:anna` | person | **weak, ambiguous** | two people are called "Anna"; the resolver correctly refused to guess |

Those last three are how you tell the identity resolution still works. If Priya merges into an email cluster, or `name:anna` disappears into one of the two Annas, something is broken.

---

## 7. Running the app

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_app.ps1
```

Backend on `http://127.0.0.1:8000`, frontend on `http://127.0.0.1:5173`. The SQL viewer runs separately on port 5000 via `.\scripts\run_viewer.ps1`, and holds the six import buttons.

Backend endpoints:

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/graph?source=<filter>` | GET | Graph for one filter, or `All`. |
| `/api/neo4j/status` | GET | Connectivity. |
| `/api/references` | GET | Current derived edges plus pipeline timestamps. |
| `/api/references/extract` | POST | Runs the reference extraction pass. |
| `/api/viewer/start`, `/api/viewer/stop` | POST | Controls the SQL viewer child process. |
| `/api/ai/chat` | POST | Optional chat agent. See section 8. |

Filters: `All`, `Mail`, `Slack`, `Teams`, `Issues`, `Docs`, `PRs`, `References`.

**Rebuilding from scratch.** SQL is the source of truth. Seed data loads from a `.sql` file with `psql -1 -v ON_ERROR_STOP=1 -f <file>`; seed files are not in the repository because `.gitignore` excludes `*.sql`. The graph rebuilds by pressing the six import buttons, which also recreate the Neo4j constraints. `scripts/setup_postgres_schema.py` **drops every table** before recreating them — it is a reset script, not a migration, and running it on a populated database destroys everything.

---

## 8. Known issues and loose ends

**The chat agent is not functional.** `backend/langgraph_agent/agent.py` needs `langgraph`, which is not installed in the venv, and an `OPENAI_API_KEY`, which is not in `.env`. `/api/ai/chat` answers `503` with an explanatory message. This is contained: the agent is imported inside the endpoint, so the graph API is unaffected. Do not install anything without asking the user first — this is a hard rule in their global instructions.

**`AGENTS.md` and `README.md` say nine tables.** One line each. Still unfixed because every task since has scoped them out.

**`backend/app.py` runs with `debug=True`.** Convenient, since the reloader picks up changes without a restart. Not appropriate outside development.

**`slack-006` looks duplicated in the reference table.** It is not. The message was edited, so it exists as two `SlackMessage` nodes carrying the same display name. The graph key includes `version_number`; the display name does not.

---

## 9. What the graph still does not model

- No chunking. Text bodies are not split into `Chunk` nodes.
- No embeddings, no vector index, no fulltext index.
- No semantic links. `MENTIONS_*` says a text names an entity. Nothing says a PR *implements* an issue or that a decision *caused* a delay. No `Topic` or `Event` nodes.
- No `File` or `Commit` nodes; `CodeChange` carries `file_path` as a property.
- No `PRVersion` nodes.
- Mail reply chains are a property, not a relationship.
- Transcript segment order is `sequence_number`, not `NEXT_SEGMENT`.

The next task in the sequence was described as an LLM-driven extraction building `Topic` and `Event` nodes. Nothing of it has been started.

---

## 10. How to work here

**Scope discipline is what has kept this project coherent.** Each task document carries an explicit "out of scope" list. Treat it as binding. If something outside it appears to need changing, **report it rather than change it**.

There is one precedent for breaking that rule, and it sets the bar. In Task 02, moving two columns out of `issue_versions` broke a query in `viewer/person_identity.py`, which was out of scope. Left alone, every import in the project would have failed with a missing-column error, including the five unrelated to issues. The query was re-pointed, the resolution logic untouched, and the change declared in its own section of the report. That is the bar: the project is otherwise non-functional, the change is mechanical, and you say so loudly.

**Deliver a report** in the project root, matching the existing ones:

1. The code change, file by file, with the reasoning.
2. The verification output, actually run. Counts per label, before and after. Never describe output you expect.
3. Documentation updates to both handoff documents wherever behaviour changed.
4. An explicit out-of-scope section: what you found and left, and anything you changed despite the scope, with the justification.

**Always re-run the Task 01 duplicate check after touching person code:**

```cypher
MATCH (a:Person), (b:Person)
WHERE a.person_key < b.person_key
  AND (
    any(e IN a.emails WHERE e IN b.emails)
    OR any(s IN a.source_ids WHERE s IN b.source_ids)
  )
RETURN a.person_key, b.person_key
```

Zero rows, always.

**Check the viewer itself, not only the backend API.** All six sources in both `detail` and `table` view. Task 02 was once reported complete while the viewer's own issue pages returned HTTP 500, because only the backend had been checked.

**Confirm idempotency.** Run each import twice and show that no count changes on the second run.

If a report claims success the numbers do not support, that is worse than no report at all.
