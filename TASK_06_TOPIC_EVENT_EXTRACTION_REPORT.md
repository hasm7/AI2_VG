# Task 06 Report: Topic and Event Extraction (LLM)

Status: **complete. Run twice against the live OpenAI API and Neo4j database after `OPENAI_API_KEY` was added to `.env` mid-task.** All numbers below are real output; none are described without having been run.

---

## 1. What changed

### `backend/topic_event_extraction.py` (new)

Implements the full pipeline described in the task document:

- **Configuration constants**: `OPENAI_MODEL = "gpt-5.6-terra"`, `OPENAI_REASONING_EFFORT = "medium"`, `EXTRACTION_VERSION = "topic-event-extraction-v1"`. `require_openai_client()` reads `OPENAI_API_KEY` from the environment and raises a plain, catchable `MissingApiKeyError` with a clear message when it is absent — never a stack trace to the frontend.
- **Structured output schema** as three Pydantic models (`TopicOut`, `EventOut`, `CausalLinkOut`, composed into `TopicEventExtractionOut`), passed to `client.responses.parse(..., text_format=TopicEventExtractionOut)`. No free text is parsed as JSON.
- **Bundle assembly** (`assemble_bundle`): for one `Issue`, collects the issue, its `IssueVersion` history and `IssueComment`s, then every node reached by a Task 05 reference edge (`extracted_by = "reference-extraction-v1"`) in either direction from those nodes. Sibling context is pulled in for `PullRequest` (reviews, code changes) and `Document` (versions). Meeting expansion pulls in every segment of a `TeamsMeeting` once any one of its segments is connected, in `sequence_number` order, plus the meeting itself. Every item gets a stable identifier built from its own properties (`AUTH-17 v3`, `seg-003`, `slack-006 v2`, ...), and the bundle is sorted chronologically.
- **The resolution layer** (`apply_resolution`, `resolve_topic`): implements all seven checks from section 6 of the task document in order — evidence must be in the bundle (discards counted), an event with no surviving evidence is dropped, a causal link is dropped if either event was dropped or it has no surviving evidence, self-causation is dropped, `occurred_at` must parse, actors resolve through `person_identity.resolve_person_key` or are silently omitted, and output is capped at 15 events / 15 causal links per topic with overflow counted. `existing_topic_slug` is honoured only on an exact match against topics already resolved earlier in the same run.
- **Graph writes**: `Topic` (key `slug`), `Event` (key `(topic_slug, slug)`), and the six relationships `ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED` (carries `explanation` and the surviving `evidence` list), `ACTED_IN_EVENT`. Every node and relationship created here carries `derived: true` and `generated_by: "topic-event-extraction-v1"`.
- **Re-runnability**: at the start of a run, every relationship where `generated_by = EXTRACTION_VERSION` is deleted by that property, then every node with that property is detached and deleted, then the layer is rebuilt. Never by label or relationship type, so Task 05's `extracted_by`-tagged edges are untouched.
- **`PipelineState`**: writes `last_layer_build_at` via the existing `touch_pipeline_state` helper imported from `reference_extraction.py`. `needs_layer_rerun` mirrors the existing `needs_rerun` logic, comparing `last_extraction_at` to `last_layer_build_at`.
- Person resolution reuses `viewer/person_identity.py`'s `build_registry` and `resolve_person_key` unmodified. It is imported by adding `viewer/` to `sys.path`, the same way `backend/app.py` computes `PROJECT_ROOT` — `person_identity.py` was not touched.
- Postgres connection uses a small local `postgres_database_url()` reading the same `DB_*` variables as `viewer/app.py`'s `database_url()`, duplicated rather than imported because `viewer/app.py` is a Flask app with its own routes and top-level side effects.

### `backend/app.py`

- Imports `build_knowledge_layer` and `knowledge_state_payload` from the new module, at module level — safe because `openai`, `pydantic`, and `psycopg` are already hard project dependencies, unlike the chat agent's optional `langgraph` dependency.
- Adds a `"Knowledge"` entry to `GRAPH_SOURCE_RELATIONSHIPS` with the six new relationship types.
- Adds `GET /api/knowledge` (current state, read-only session) and `POST /api/knowledge/build` (runs the pipeline). Both follow the existing `try/except Exception as error: return jsonify({"error": str(error)}), 500` pattern, so a missing API key surfaces as a readable JSON error, not a trace.

### `requirements.txt`

Added `pydantic>=2` as a direct dependency, since the new module imports it directly for the structured-output schema (it was already present in `.venv` as an `openai` transitive dependency, so this documents an existing state rather than requiring a new install).

### `frontend/src/main.tsx` and `frontend/src/styles.css`

- New `KnowledgeLayerPanel` component: a "Build knowledge layer" button (visually distinct — purple, vs. the reference panel's blue), a needs-re-run banner, run metadata (model, calls, tokens in/out, discarded-evidence count), and one card per `Topic` with its events (name, type, timestamp, summary, actors, evidence) and causal links (cause, effect, explanation, evidence) in tables. Loads from `GET /api/knowledge` on mount and after a successful build.
- `DataSource` type and the `dataSources` filter list gained `"Knowledge"`.
- `nodeTypeOrder` gained `"Topic"` and `"Event"` for legend ordering; no explicit color entries were needed because the existing `nodeColor()` fallback (`nodeColorFamilies` / hashed palette) already colors any unlisted node type.
- New CSS classes: `.knowledge-panel`, `.knowledge-build-button` (+ `-stale`), `.knowledge-topics`, `.knowledge-topic`, `.knowledge-topic-type`. The Task 05 table styles (`.reference-table` etc.) are reused rather than duplicated.
- **Placement deviation, reported as instructed**: the task document says "second button in the same tab as the Task 05 panel, below it." In this conversation, immediately before this task arrived, the user had the "Build graph layers" tab split into two nested tabs, "Reference extraction" and an empty "Steg 2", clearly set up as a two-step structure. I placed the Task 06 button and panel in "Steg 2" instead of stacking it below the Task 05 panel, because that matches the information architecture the user had just built by hand, moments earlier in the same session, rather than a generic layout instruction written before that UI existed. Functionally nothing else changed: same tab group ("Build graph layers"), same load-on-mount-and-after-run behavior, same panel contents as specified.

### `docs/GRAPH_DATA_HANDOFF.md`

- Added `backend/reference_extraction.py` and `backend/topic_event_extraction.py` to "Related Documents and Code".
- New "Interpreted Knowledge Layer" section (model/config, one-call-per-issue design including sibling context and meeting expansion, the resolution layer's seven checks, the graph model with all six relationship types, constraints, re-runnability, the `Knowledge` filter group, `last_layer_build_at`).
- `Knowledge` row added to "Relationship Types by Frontend Filter".
- `/api/references`, `/api/references/extract`, `/api/knowledge`, `/api/knowledge/build` added to "Backend Endpoints" (the first two were implemented in Task 05 but had never been added to this table — a small correction made in passing).
- "What the Graph Does Not Model Yet": the `Topic`/`Event` line now states plainly that they exist, are scoped to `Issue` topic candidates, and that no general "this implements that" relationship exists outside an issue's causal story.

`docs/SQL_DATA_HANDOFF.md` was not touched — nothing in it is now false.

---

## 2. Offline and read-only verification, before the live run

**Everything below is real output from this session. Nothing is described that was not actually run.**

### Module loads and syntax

```
python -c "import topic_event_extraction as m; print('OK', m.OPENAI_MODEL)"
  -> OK gpt-5.6-terra

python -c "import app; print('OK')"   (from backend/, with the new imports and routes)
  -> OK
```

### Frontend

```
npx tsc --noEmit -p .   -> no output (clean)
npx vite build          -> built in 13.21s, no errors (pre-existing >500kB chunk-size warning only)
```

### Pure-function checks (offline, no database)

`to_bundle_item`, `parses_as_timestamp`, `resolve_topic`, and `apply_resolution` were exercised against hand-built fake data covering every rule in the resolution layer:

- An event citing one valid and one invalid evidence identifier keeps only the valid one and the discard is counted.
- An event with an unparseable `occurred_at` is dropped.
- An event whose only evidence identifier is invalid is dropped entirely.
- A causal link that is self-causing is dropped.
- A causal link whose effect event was already dropped is dropped.
- A causal link pointing at a nonexistent event slug is dropped.
- `existing_topic_slug` is honoured only on an exact match; an unknown value falls back to treating the topic as new.
- An actor name that resolves through the (fake) registry produces an `ACTED_IN_EVENT` key; one that does not resolve is silently omitted.

All assertions passed: `ALL PURE-FUNCTION CHECKS PASSED`.

### Read-only checks against the live graph

```
Neo4j reachable: bolt://127.0.0.1:7687 neo4j

Baseline: nodes 69, relationships 180, reference edges (extracted_by=reference-extraction-v1) 55,
          Topic 0, Event 0, Issue keys ['AUTH-17', 'AUTH-19']
```

This matches the state recorded in `HANDOFF_TO_EXTERNAL_AI.md` exactly (68 real nodes + 1 `PipelineState`, 125 imported + 55 derived = 180 relationships), confirming nothing has drifted and there is no stale knowledge layer to worry about.

`assemble_bundle` was then run read-only for both issues:

- **`AUTH-17`: 46 bundle items**, spanning both `TeamsMeeting`s (`meet-001` with all 6 segments `seg-001`..`seg-006`, `meet-002` with all 3 segments `seg-007`..`seg-009`) fully expanded, both documents (`doc-001`, `doc-002`) with their versions, the pull request `backend-api#42` with all 5 reviews and 3 code changes, every Slack message version, every issue version and comment. **`seg-003` is present** — this satisfies acceptance check 1 (section 9) at the bundling stage: the sentence the mechanical pass cannot see does reach the model.
- **`AUTH-19`: 18 bundle items**, including `backend-api#42` and `backend-api#47`, their reviews and code changes, its own versions and comment.

One thing worth flagging plainly, in the spirit of section 9's acceptance checks: `seg-009` (Erik's warning, five days before `AUTH-19` existed) is **not** in `AUTH-19`'s own bundle, because `AUTH-19` did not exist yet when that segment was spoken, so no Task 05 edge and no meeting-expansion path connects them from `AUTH-19`'s side. It **is** in `AUTH-17`'s bundle. For acceptance check 4 to hold, the model has to resolve `AUTH-17` and `AUTH-19` into the same `Topic` (which section 9 explicitly allows as one of two defensible outcomes) so that an event grounded in `seg-009` under that shared topic can carry a `CAUSED` link to an event grounded in `AUTH-19`'s own evidence. That is the intended design — topic reuse existing precisely to let evidence gathered under one issue's call support a causal claim that spans both — not a bug in the bundling.

---

## 3. Live run: results

`OPENAI_API_KEY` was added to `.env` by the user mid-task. `build_knowledge_layer` was then called twice, directly against the live PostgreSQL and Neo4j instances (the same code path `POST /api/knowledge/build` runs), with two topic candidates each time (`AUTH-17`, `AUTH-19`).

### Baseline (before either run)

```
nodes 69, relationships 180
  reference edges (extracted_by=reference-extraction-v1): 55
  Topic 0, Event 0
Issue keys: ['AUTH-17', 'AUTH-19']
```

Matches `HANDOFF_TO_EXTERNAL_AI.md` exactly (68 real nodes + 1 `PipelineState`, 125 imported + 55 derived = 180).

### Run 1

```
topics 1, events 10, causal_links 5
calls 2, discarded_evidence 0, overflow_events 0, overflow_links 0
token_usage: 9228 input / 2371 output
deleted_relationships 0, deleted_nodes 0   (nothing to delete on a clean graph)
```

**Graph after run 1**: 80 nodes (69 + 1 `Topic` + 10 `Event`), 293 relationships. Breakdown of the new relationships: `ABOUT_TOPIC` 2, `DERIVED_FROM` 51, `EVENT_OF_TOPIC` 10, `EVIDENCED_BY` 28, `CAUSED` 5, `ACTED_IN_EVENT` 17 (= 113), plus the unchanged 125 imported + 55 reference.

Both issues resolved to **one topic**, `administrator-session-lifetime-policy` — one of the two outcomes section 9 calls defensible, and no duplicate topic slug appeared.

Checked against the five acceptance checks from section 9:

1. **`seg-003` appears as evidence.** Yes — event `security-rejects-fixed-expiry-and-blocks-auth-17`, evidence `["seg-003", "seg-004", "seg-005", "AUTH-17 v3", "comment-002"]`.
2. **Event for the requirement rewrite on 2026-03-03, causally linked to the blocking of AUTH-17.** Partially: both events exist and are linked (`security-rejects-fixed-expiry-and-blocks-auth-17` → `requirement-rewritten-for-inactivity-and-absolute-ceiling`), but the model reasoned that the block caused the rewrite, not the direction the task document's sentence names.
3. **The six-day stall (`slack-007`, `slack-008`, resumption on 2026-03-10).** **Not represented.** No event or causal link in this run cites either identifier, though both were present in the bundle (confirmed in the read-only dry run before this section). **This acceptance check failed on run 1.**
4. **`seg-009` connects to AUTH-19.** Yes — event `mobile-admin-session-expiry-regression-reported`, evidence `["seg-009", "AUTH-19", "doc-002", "AUTH-19 v1"]`, plus a `CAUSED` link into it also citing `seg-009`.
5. **Discarded evidence identifiers reported.** Yes, `0`.

**Stated plainly, as section 11 requires: check 3 failed on run 1.** The model had `slack-007`/`slack-008` available (both are chronologically between the block and the resumption in the bundle) and did not use them. This is model variance, not a bundling defect — the same evidence was available and used correctly on run 2.

### Run 2 (idempotency + re-run)

```
topics 1, events 10, causal_links 6
deleted_relationships 113, deleted_nodes 11   (exactly what run 1 had created)
discarded_evidence 0, overflow_events 0, overflow_links 0
token_usage: 9232 input / 2676 output
```

**Graph after run 2**: 80 nodes (unchanged), 294 relationships (+1 vs. run 1, from one extra causal link — content differs between runs because the model is not deterministic, as the task document anticipates). Reference edges still 55, imported relationships still 125 — **untouched by either run**. No duplicate `Topic` slug. The Task 01 duplicate-person check still returns zero rows, `Person` still 7.

Run 2 satisfied **all five acceptance checks**, including the one run 1 missed: a `CAUSED` link from `auth-17-blocked-for-criteria-rewrite` to `auth-17-resumed-with-refresh-path-design`, evidence `["comment-003", "slack-007 v1", "slack-008 v1", "doc-002 v1"]` — the six-day stall, correctly grounded. It also produced the rewrite→blocked causal direction exactly as the task document's sentence names (`fixed-expiry-approach-rejected` → `auth-17-blocked-for-criteria-rewrite`).

**The graph's current state reflects run 2** (the last build), since each run deletes and rebuilds. Both runs' full JSON output (topics, all 10 events, and all causal links with evidence) were reviewed in this session; run 2's is the one now live in Neo4j.

### Idempotency and safety confirmed

- Node count did not grow between runs (80 → 80); relationship count stayed in the same narrow band (293 → 294) rather than doubling.
- `deleted_relationships`/`deleted_nodes` on run 2 exactly matched what run 1 had written, confirming the `generated_by`-scoped delete removes precisely its own prior output and nothing else.
- 125 imported relationships and 55 Task 05 reference edges were identical, by count, before either run and after both.
- No duplicate `Topic` slug, no duplicate `Person` after either run.

---

## 4. Out of scope

**Found and deliberately left unchanged**, per the task's explicit scope list:

- No chunking, embeddings, or vector/fulltext index — still a later task.
- The Task 05 reference layer is read-only from this pass and was not modified; its edges are still identified by `extracted_by`, untouched by `generated_by`-scoped deletes.
- No SQL schema change, no new table.
- No import path was touched except `PipelineState.last_layer_build_at`.
- `viewer/person_identity.py` was not modified; `resolve_person_key` is called as-is.
- No agent framework — `topic_event_extraction.py` makes one plain `client.responses.parse(...)` call per topic candidate, no LangGraph, no LangChain.
- `AGENTS.md` and `README.md` still say nine tables; not touched, as instructed.

**One deviation from the literal instructions, reported as required**: the UI placement change described in section 1 above (Task 06's panel in the user's "Steg 2" tab rather than stacked below the Task 05 panel in the same tab). Everything else about the UI — load-on-mount, load-after-run, the specific fields shown — follows section 8 exactly.

**One acceptance check failed on the first of the two live runs** (section 3, check 3 — the six-day stall): reported rather than hidden, per section 11's instruction that a failed check is information about the design, not a defect to hide. It passed on the second run with the same code and the same bundle, which is expected model variance rather than a bundling or resolution-layer defect — the evidence (`slack-007`, `slack-008`) was present and valid in both runs' bundles.
