# Layer 3: Architecture Layer

The third graph-building step. Part A derives the code structure (`Repository` -> `Module` -> `File`) from the file
paths of the code changes, deterministically. Part B asks a model, once per repository, for the logical `Component`s
of the system (an endpoint, a service, a data store, a library, ...) and the `DEPENDS_ON` edges between them, grounded
in that repository's PRs, code changes, reviews, documents and messages.

**Verified on 2026-09-29** against `backend/architecture_layer.py`, `backend/app.py`, `frontend/src/main.tsx` and the
live graph.

Code: `backend/architecture_layer.py`. UI: `ArchitectureLayerPanel`, tab `Architecture layer` (third of seven).

## 1. Why it exists

`CodeChange` nodes carry a file path as a string. "Which file changed" and "which part of the system does this affect"
are different questions, and later layers reason about components: root causes sit in components, events affect
components, people have expertise in components.

## 2. Configuration

| Constant | Value |
| --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra`, reasoning effort `medium`, structured output |
| `ARCHITECTURE_VERSION` | `architecture-layer-v1` |
| `MAX_COMPONENTS` | 20 per repository |
| `MAX_DEPENDENCIES` | 30 per repository |
| `TRUNCATE_LENGTH` | 4000 characters per text field in the bundle (including `diff`) |

Prerequisite: `PipelineState.last_extraction_at` (else 409 `Run Reference extraction first.`). Missing
`OPENAI_API_KEY`: 503.

## 3. Part A: structure from file paths

Read every `(PullRequest)-[:HAS_CODE_CHANGE]->(CodeChange)`, and for each code change:

- `Repository` key `(source_instance, repository)` from the code change (or its PR).
- `Module` key `(source_instance, repository, path)` where `path` is everything before the **last** `/` of the file
  path, or `(root)` when there is none. One module level only: `app/integrations/fortnox/client.py` gives module
  `app/integrations/fortnox`; there is no `app/integrations` or `app` module above it. `display_name` `<repository>:<path>`.
- `File` key `(source_instance, repository, path)` with `file_name`, `extension` (after the last `.`, empty if
  none), `change_count` (number of `CodeChange` nodes on that path, across all PR versions), `display_name` = path.
- `(CodeChange)-[:MODIFIES_FILE]->(File)` for every code change.

A repository exists only if at least one of its PRs has a code change. No component, module or file comes from
anywhere else.

## 4. Part B: components per repository

### 4.1 Bundle

For repository R (`assemble_repository_bundle`), items keyed by identifier, first one wins:

1. Every `PullRequest` of R (identifier `display_name`, text: title, description, state).
2. Every `CodeChange` of those PRs (identifier `<display_name> v<version_number>`, text: `file_path (change_type)`,
   before, after, diff).
3. Every `PullRequestReview` of those PRs (identifier `source_id`, text: body).
4. Every `Document` linked by `MENTIONS_DOCUMENT` (either direction) to those PRs, code changes or reviews
   (identifier `document_id`, text: title + body).
5. **Every** `Document` with `document_type = "technical-design"`, whatever repository it is about.
6. Every `SlackMessage`, `MailMessage` or `TeamsTranscriptSegment` that names one of R's PRs (`MENTIONS_PULL_REQUEST`),
   and every document those nodes name.

All text fields are cut to 4000 characters. The model also gets `file_paths`, the sorted list of R's `File.path`
values, as the only allowed file paths.

### 4.2 Output schema

```text
ArchitectureExtractionOut
  components[]:   slug, name, component_type (service|endpoint|job|data_store|library|ui|external_system|other),
                  summary, file_paths[], evidence[]
  dependencies[]: from_component_slug, to_component_slug,
                  dependency_type (calls|reads_from|writes_to|shares_logic_with|depends_on), explanation, evidence[]
```

The instructions ask for components supported by the bundle only (no general knowledge), a component being a logical
part with its own responsibility (a file alone is not a component unless it has one), evidence copied exactly, file
paths copied from the list, short lowercase hyphenated slugs.

### 4.3 Validation (in this order, per repository)

1. Evidence identifiers not in the bundle are discarded.
2. File paths not exactly in R's file list are discarded.
3. A component with no evidence left is dropped.
4. A duplicate component slug is dropped (first kept).
5. At most 20 components are kept.
6. A dependency whose end was dropped, or that points to itself, is dropped.
7. A dependency with no evidence left is dropped.
8. A duplicate `(from, to, dependency_type)` is dropped.
9. At most 30 dependencies are kept.

Dependencies exist only **inside one repository**: there is no way to express a dependency from a component in one
repository to a component in another.

## 5. Graph model

```cypher
(:Repository)-[:CONTAINS_MODULE]->(:Module)
(:Module)-[:CONTAINS_FILE]->(:File)
(:CodeChange)-[:MODIFIES_FILE]->(:File)
(:Component)-[:PART_OF_REPOSITORY]->(:Repository)
(:Component)-[:IMPLEMENTED_IN]->(:File)
(:Component)-[:DEPENDS_ON {dependency_type, explanation, evidence}]->(:Component)
(:Component)-[:COMPONENT_EVIDENCED_BY]->(:SourceNode)
```

`Component` key `(source_instance, repository, slug)`: `name`, `display_name`, `component_type`, `summary`, `model`.
Every node and relationship: `derived`, `generated_by = architecture-layer-v1`, `generated_at`. Constraints
`repository_key`, `module_key`, `file_key`, `component_key`. `COMPONENT_EVIDENCED_BY` is its own type so it never
mixes with the Knowledge layer's `EVIDENCED_BY`. Layer 6 later adds metric properties to `Component`.

## 6. Build behaviour

1. Check the prerequisite and the API key; ensure constraints.
2. Compute Part A in Python.
3. For each repository: assemble the bundle, call the model, validate. **If any call fails, nothing is changed**.
4. Only after all calls succeeded: delete every relationship, then every node, with
   `generated_by = architecture-layer-v1` (`DETACH DELETE` also removes relationships of layers 4 to 6 attached to
   components: see `docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 5).
5. Write repositories, modules, files, `MODIFIES_FILE`, then components with `PART_OF_REPOSITORY`, `IMPLEMENTED_IN`,
   `COMPONENT_EVIDENCED_BY`, then `DEPENDS_ON`.
6. Set `PipelineState.last_architecture_build_at`.

## 7. Current state (2026-09-29)

Kvitta data. 3 repositories, 13 modules, 23 files, 39 `MODIFIES_FILE`, 18 components, 13 dependencies, 17
`IMPLEMENTED_IN`, 65 `COMPONENT_EVIDENCED_BY`; 57 nodes and 188 relationships; not stale. Part A matches a
recomputation from the file paths exactly, and every Part B rule check passes (no cross-repository dependency, all
evidence inside the repository's bundle).

| Repository | Modules | Files | Components |
| --- | --- | ---: | ---: |
| `kvitta-api` | `app/receipts`, `app/expenses`, `app/approval`, `app/integrations/fortnox`, `app/payouts`, and the matching five `tests/...` directories | 17 | 11 |
| `kvitta-mobile` | `src/capture`, `src/offline` | 4 | 4 |
| `kvitta-web` | `src/approval` | 2 | 3 |

| Repository | Component | Type | Files |
| --- | --- | --- | --- |
| `kvitta-api` | Receipt reader | service | `app/receipts/reader.py` |
| `kvitta-api` | Receipt VAT rules | library | `app/receipts/vat.py` |
| `kvitta-api` | Receipt image storage and retention | data_store | `app/receipts/storage.py` |
| `kvitta-api` | Expense upload API | endpoint | `app/expenses/api.py` |
| `kvitta-api` | Offline expense sync endpoint | endpoint | `app/expenses/api.py`, `app/expenses/sync.py` |
| `kvitta-api` | Approval routing rules | service | `app/approval/rules.py` |
| `kvitta-api` | Manager approval limits | library | `app/approval/limits.py` |
| `kvitta-api` | Fortnox authentication | service | `app/integrations/fortnox/auth.py` |
| `kvitta-api` | Fortnox client | service | `app/integrations/fortnox/client.py` |
| `kvitta-api` | Fortnox | external_system | none |
| `kvitta-api` | Payout export | service | `app/payouts/export.py` |
| `kvitta-mobile` | Expense upload | service | `src/capture/upload.ts` |
| `kvitta-mobile` | Offline expense queue | service | `src/offline/queue.ts`, `src/offline/queue.test.ts` |
| `kvitta-mobile` | Retry delivery | service | `src/offline/retry.ts` |
| `kvitta-mobile` | Expense sync endpoint | external_system | none |
| `kvitta-web` | Approval List | ui | `src/approval/ApprovalList.tsx` |
| `kvitta-web` | Approval Limits Client | service | `src/approval/limits.ts` |
| `kvitta-web` | Approval Limits API | endpoint | none |

`DEPENDS_ON` (all `calls` unless noted): Expense upload API -> Offline expense sync endpoint, Receipt reader, Receipt
image storage and retention; Receipt reader -> Receipt VAT rules; Approval routing rules -> Manager approval limits;
Fortnox client -> Fortnox authentication and Fortnox; Fortnox authentication -> Fortnox; Expense upload -> Offline
expense queue; Offline expense queue -> Retry delivery (`depends_on`); Retry delivery -> Expense sync endpoint;
Approval List -> Approval Limits Client (`depends_on`); Approval Limits Client -> Approval Limits API.

Because dependencies cannot cross repositories, the model represented the server as a file-less component inside each
client repository ("Expense sync endpoint" in `kvitta-mobile`, "Approval Limits API" in `kvitta-web`), and the
external service Fortnox as one inside `kvitta-api`. The seven test files under `tests/` belong to no component.
Component names vary between rebuilds.

## 8. What the data needs for this layer

- **Code changes with realistic file paths**, several directories per repository, reused across PRs, so modules,
  files with several changes, and components spanning more than one file appear.
- **More than one repository** (for example a backend service, a mobile or web client, a shared library, an
  infrastructure repository), each with several PRs, so several component graphs are built.
- **Text that says how parts relate**: PR descriptions, reviews and technical-design documents saying "the mobile
  client calls the token refresh before every request", "reads from the image store", "the job writes to ...". Dependencies need
  evidence.
- **Technical-design documents** (`document_type = 'technical-design'`) describing components and their
  responsibilities; they are in every repository's bundle.
- Messages that name PRs (`repo#123`) bring discussion about the code into the bundle.
- Test files, configuration and documentation files are fine; they get `File` nodes but usually no component.
- Keep `before_summary` / `after_summary` concrete; the diff is cut to 4000 characters.

## 9. API

`GET /api/architecture`: `last_import_at`, `last_extraction_at`, `last_architecture_build_at`, `needs_rerun`,
`stale_reasons`, `counts` (`repositories`, `modules`, `files`, `components`, `dependencies`), `repositories`
(`source_instance`, `name`, `module_count`, `component_count`), `modules` (`repository`, `path`, `file_count`),
`files` (`repository`, `module`, `path`, `change_count`, `modified_by`, `components`), `components` (`repository`,
`slug`, `name`, `component_type`, `summary`, `files`, `evidence`), `dependencies` (`from_name`, `to_name`,
`dependency_type`, `explanation`, `evidence`), `relationships` (per type created by this layer, with start and end
labels read from the graph and a count).

`POST /api/architecture/build`: the same plus `built_at`, `deleted_relationships`, `deleted_nodes`, `calls`, `model`,
`discarded_evidence`, `discarded_file_paths`, `token_usage`. Errors 409, 503, 500.

## 10. UI

Tab `Architecture layer`. Button `Build architecture layer` / `Building architecture layer...`. Description:
`Models how the code is structured and how its components depend on each other.` Counts under `Nodes and
relationships:`. Tables: `Repositories (node)`, `Modules (node)`, `Files (node)`, `Components (node)`,
`Relationships (all relationship types)`, `Dependencies (relationship: DEPENDS_ON)`. Empty state: `No architecture
layer yet. Press the button to build it.`

Graph filter `Architecture`: `CONTAINS_MODULE`, `CONTAINS_FILE`, `MODIFIES_FILE`, `IMPLEMENTED_IN`,
`PART_OF_REPOSITORY`, `DEPENDS_ON`, `COMPONENT_EVIDENCED_BY`.

## 11. Boundaries

- Reads and writes Neo4j only; never PostgreSQL.
- Deletes only by `generated_by = architecture-layer-v1`; never uses `EVIDENCED_BY`.
- Must run before layers 4, 5 and 6, which need `Component` nodes.
