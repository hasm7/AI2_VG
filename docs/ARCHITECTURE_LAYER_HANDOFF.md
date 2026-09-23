# Architecture Layer Handoff

This document describes the Architecture layer: the third middle-panel graph-building step that models the deterministic code structure (`Repository` -> `Module` -> `File`) and the LLM-derived logical `Component` graph on top of it.

The layer is implemented in `backend/architecture_layer.py`, exposed by `backend/app.py`, and displayed in `ArchitectureLayerPanel` inside `frontend/src/main.tsx`.

## Purpose

`CodeChange` and `PullRequest` nodes carry `file_path` as a flat string property. There is no first-class notion of a repository, a module, or a logical component anywhere in the imported graph.

The Architecture layer adds that structure in two parts, run by one button:

- **Part A (deterministic):** derives `Repository`, `Module`, and `File` nodes purely from `CodeChange.file_path`, with `MODIFIES_FILE` edges back to the code changes that touched each file.
- **Part B (LLM):** one model call per `Repository` proposes logical `Component` nodes (an endpoint, a service, a data store, a shared library, etc.) and `DEPENDS_ON` edges between them, grounded in the PRs, code changes, reviews, related documents and messages for that repository.

It exists because "which file changed" and "which part of the system this affects" are different questions. Downstream layers (Causal, Collaboration) reason about components, not raw files.

## Current Local State

From `/api/architecture`:

| Field | Current value |
| --- | --- |
| Repositories | 1 |
| Modules | 2 |
| Files | 4 |
| Components | 3 |
| Dependencies | 2 |
| `last_import_at` | `2026-09-20T13:18:12.864348+00:00` |
| `last_extraction_at` | `2026-09-23T18:08:36.712761+00:00` |
| `last_architecture_build_at` | `2026-09-23T18:09:26.465345+00:00` |
| `needs_rerun` | `false` |
| `stale_reasons` | `[]` |

Current repository: `backend-api`, with modules `backend/auth` and `tests/auth`.

Current components:

| Repository | Component | Type | Files |
| --- | --- | --- | --- |
| `backend-api` | Mobile session refresh endpoint | endpoint | `backend/auth/mobile_refresh.py` |
| `backend-api` | Session validity policy | service | `backend/auth/session.py` |
| `backend-api` | Web session refresh endpoint | endpoint | `backend/auth/refresh.py` |

Current dependencies:

| From | To | Type |
| --- | --- | --- |
| Mobile session refresh endpoint | Session validity policy | calls |
| Web session refresh endpoint | Session validity policy | calls |

`tests/auth/test_session.py` is tracked as a `File` (2 changes) but is not `IMPLEMENTED_IN` by any component, which is expected: a test file is not itself a logical component.

## Model and Configuration

| Constant | Value |
| --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra` |
| `OPENAI_REASONING_EFFORT` | `medium` |
| `ARCHITECTURE_VERSION` | `architecture-layer-v1` |
| `MAX_COMPONENTS` | 20 (per repository) |
| `MAX_DEPENDENCIES` | 30 (per repository) |

The OpenAI Responses API is used with structured output parsing into `ArchitectureExtractionOut`. If `OPENAI_API_KEY` is missing, `/api/architecture/build` returns `503`.

## Graph Model

Part A:

```cypher
(:Repository)-[:CONTAINS_MODULE]->(:Module)
(:Module)-[:CONTAINS_FILE]->(:File)
(:CodeChange)-[:MODIFIES_FILE]->(:File)
```

Part B:

```cypher
(:Component)-[:PART_OF_REPOSITORY]->(:Repository)
(:Component)-[:IMPLEMENTED_IN]->(:File)
(:Component)-[:DEPENDS_ON {dependency_type, explanation, evidence}]->(:Component)
(:Component)-[:COMPONENT_EVIDENCED_BY]->(:SourceNode)
```

`COMPONENT_EVIDENCED_BY` is a distinct relationship type from the Knowledge layer's `EVIDENCED_BY`, so the two layers never mix under the same filter.

Every node and relationship created by this layer is stamped with `derived: true`, `generated_by: "architecture-layer-v1"`, `generated_at`. `Component` also stores `model`.

### `Repository`

Key: `(source_instance, name)`. Properties: `source_instance`, `name`, `display_name` (= `name`).

### `Module`

Key: `(source_instance, repository, path)`. `path` is the directory part of `file_path` (everything before the last `/`, or `(root)` if there is none). Only one module level is created. Properties: `display_name` = `<repository>:<path>`.

### `File`

Key: `(source_instance, repository, path)`. Properties: `file_name`, `extension`, `change_count` (number of `CodeChange` nodes pointing to it), `display_name` = `path`.

### `Component`

Key: `(source_instance, repository, slug)`. Properties: `name`, `display_name`, `component_type` (one of `service`, `endpoint`, `job`, `data_store`, `library`, `ui`, `external_system`, `other`), `summary`, `model`.

## Bundle Assembly (Part B, per repository)

For repository R, the bundle is, in order: every `PullRequest` in R; every `CodeChange` of those PRs; every `PullRequestReview` of those PRs; the full list of `File.path` values in R (a reference list, not evidence); every `Document` reached via `MENTIONS_DOCUMENT` from the PR/CodeChange/Review nodes, or from a node that mentions one of those PRs via `MENTIONS_PULL_REQUEST`; every `Document` with `document_type = "technical-design"`; every `SlackMessage`/`MailMessage`/`TeamsTranscriptSegment` that mentions a PR in R. Only `MENTIONS_*` edges with `extracted_by = "reference-extraction-v1"` count. All text fields, including `CodeChange.diff`, are truncated to 4000 characters.

Evidence identifiers follow the layer-wide rules (see `CAUSAL_LAYER_HANDOFF.md` for the full identifier table), with `CodeChange` identified as `<display_name> v<version_number>`.

## Validation

Applied in this exact order per repository:

1. Evidence identifiers not in the bundle are discarded.
2. `file_paths` not exactly matching a `File.path` in R are discarded.
3. Components with no remaining evidence are dropped.
4. Duplicate component slugs are dropped (first kept).
5. At most the first 20 components are kept.
6. Dependencies where either component was dropped are dropped.
7. Self-dependencies (`from == to`) are dropped.
8. Dependencies with no remaining evidence are dropped.
9. Duplicate `(from, to, dependency_type)` triples are dropped (first kept).
10. At most the first 30 dependencies are kept.

## Constraints

```cypher
CREATE CONSTRAINT repository_key IF NOT EXISTS FOR (n:Repository) REQUIRE (n.source_instance, n.name) IS UNIQUE
CREATE CONSTRAINT module_key IF NOT EXISTS FOR (n:Module) REQUIRE (n.source_instance, n.repository, n.path) IS UNIQUE
CREATE CONSTRAINT file_key IF NOT EXISTS FOR (n:File) REQUIRE (n.source_instance, n.repository, n.path) IS UNIQUE
CREATE CONSTRAINT component_key IF NOT EXISTS FOR (n:Component) REQUIRE (n.source_instance, n.repository, n.slug) IS UNIQUE
```

## Build/Rebuild Behavior

1. Require `last_extraction_at` to exist (else `409 {"error": "Run Reference extraction first."}`).
2. Require `OPENAI_API_KEY` (else `503`).
3. Read all `CodeChange`/`PullRequest` data and compute Part A structures in Python.
4. For each repository, assemble a bundle and call the model once. If any call fails, the existing layer is untouched and an error is returned.
5. Only after every model call has succeeded: delete the previous layer (`generated_by = "architecture-layer-v1"`, relationships then `DETACH DELETE` nodes), then write Part A and Part B.
6. Update `PipelineState.last_architecture_build_at`.
7. Return the current state plus run metadata.

This differs from the Knowledge layer's delete-first order on purpose: an Architecture rebuild must never leave a half-built or empty component graph if the model call fails partway through.

## Pipeline State

| Property | Written by | Purpose |
| --- | --- | --- |
| `last_import_at` | SQL import | Upstream for staleness. |
| `last_extraction_at` | Reference extraction | Upstream for staleness; also the build prerequisite. |
| `last_architecture_build_at` | Architecture layer | This layer's own timestamp. |

Staleness (`needs_rerun` and `stale_reasons`) is computed centrally by `backend/pipeline_staleness.py`, not by this module. This layer's upstream stages, per `UPSTREAM_BY_STAGE`, are `import` and `references`. See `GRAPH_DATA_HANDOFF.md`'s "Pipeline Staleness" section for the full rule set, including how staleness propagates transitively (for example, from a stale Knowledge layer, through Causal, into Collaboration).

## Backend API

### `GET /api/architecture`

Returns pipeline timestamps, `needs_rerun`, `stale_reasons`, `counts`, and the `components`, `dependencies`, `files` tables described in the work order.

### `POST /api/architecture/build`

Runs the build and returns the same payload plus `built_at`, `deleted_relationships`, `deleted_nodes`, `calls`, `model`, `discarded_evidence`, `discarded_file_paths`, `token_usage`.

Errors: `409` if reference extraction has not run; `503` if `OPENAI_API_KEY` is missing; `500` otherwise.

## Frontend UI

Tab `Architecture layer` inside `BuildGraphLayersPanel`, rendered by `ArchitectureLayerPanel`, third of seven inner tabs. Button text `Build architecture layer` / `Building architecture layer...`. Tables, in order: `Components`, `Dependencies`, `Files`. Empty state: `No architecture layer yet. Press the button to build it.`

## Graph Visualization Filter

```python
"Architecture": [
    "CONTAINS_MODULE",
    "CONTAINS_FILE",
    "MODIFIES_FILE",
    "IMPLEMENTED_IN",
    "PART_OF_REPOSITORY",
    "DEPENDS_ON",
    "COMPONENT_EVIDENCED_BY",
]
```

Node colors added: `Repository` (`#eab308`), `Module` (`#fb923c`), `File` (`#fbbf24`), `Component` (`#34d399`).

## Important Boundaries

- Reads and writes Neo4j only; never touches PostgreSQL.
- Deletes only by `generated_by = "architecture-layer-v1"`, never by label or relationship type.
- Never uses `EVIDENCED_BY` (reserved for the Knowledge layer); uses `COMPONENT_EVIDENCED_BY` instead.
- Does not create `Person`, `Issue`, `PullRequest`, `Document`, `Topic`, or `Event` nodes.
- The Causal, Collaboration, and Graph algorithm layers all depend on `Component` nodes existing; this layer must run before them.
