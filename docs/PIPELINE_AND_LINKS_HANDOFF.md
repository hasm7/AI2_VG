# Pipeline and Links Handoff

How the pieces connect: the order in which the graph is built, what each stage reads and writes, how staleness is
decided, what a rebuild deletes, and the paths through the graph from any source record to everything derived from
it.

**Verified on 2026-09-29** against `backend/pipeline_staleness.py`, every layer module, `backend/app.py`,
`viewer/app.py` and the live graph.

Related: `docs/SQL_DATA_HANDOFF.md`, `docs/GRAPH_DATA_HANDOFF.md`, one `docs/*_LAYER_HANDOFF.md` per layer,
`docs/AI_AGENT_HANDOFF.md`.

## 1. The flow

```text
PostgreSQL (10 tables, 6 sources)
   │  SQL viewer import, one button per source (viewer/app.py)
   ▼
Source nodes + Person identities in Neo4j
   │ 1 Reference extraction      regex, no model       MENTIONS_ISSUE / _PULL_REQUEST / _DOCUMENT
   │ 2 Knowledge layer           LLM, 1 call per issue  Topic, Event, CAUSED, EVIDENCED_BY, ...
   │ 3 Architecture layer        code + LLM, 1 call per repository
   │                                                    Repository, Module, File, Component, DEPENDS_ON, ...
   │ 4 Root cause & impact layer LLM, 1 call per topic  RootCause, CONTRIBUTED_TO, AFFECTED_COMPONENT, ...
   │ 5 Expertise & collaboration code                   Expertise, WORKS_WITH
   │ 6 Graph algorithms          networkx               Community; metrics on Person, Topic, Component
   │ 7 Embeddings                OpenAI embeddings      vectors + search indexes on 15 labels
   ▼
AI agent (Chat with AI): hybrid search into the graph, then walks the layers
```

All seven layers are run from the center tab `Build graph layers` in the app, one inner tab each, in this order:
`Reference extraction`, `Knowledge layer`, `Architecture layer`, `Root cause & impact layer`,
`Expertise & collaboration layer`, `Graph algorithms`, `Embeddings`.

## 2. Stages at a glance

| # | Stage (key) | Module | Endpoint (build) | Prerequisite (else 409) | Model calls | Deletes before writing | Timestamp |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 0 | Import (`import`) | `viewer/app.py` | `POST /import/<source>` on port 5000 | none | none | nothing (merge only) | `last_import_at` |
| 1 | References (`references`) | `reference_extraction.py` | `POST /api/references/extract` | none | none | its relationships (`extracted_by`) | `last_extraction_at` |
| 2 | Knowledge (`knowledge`) | `topic_event_extraction.py` | `POST /api/knowledge/build` | none in code (needs references to find evidence) | 1 per `Issue` (`gpt-5.6-terra`, effort medium) | its nodes and relationships, **first** | `last_layer_build_at` |
| 3 | Architecture (`architecture`) | `architecture_layer.py` | `POST /api/architecture/build` | `last_extraction_at` | 1 per repository (`gpt-5.6-terra`, medium) | its data, **after** all calls succeed | `last_architecture_build_at` |
| 4 | Root cause & impact (`causal`) | `causal_layer.py` | `POST /api/causal/build` | `last_layer_build_at` and `last_architecture_build_at` | 1 per `Topic` (`gpt-5.6-terra`, medium) | its data, **after** all calls succeed | `last_causal_build_at` |
| 5 | Expertise & collaboration (`collaboration`) | `collaboration_layer.py` | `POST /api/collaboration/build` | knowledge, architecture and causal timestamps | none | its data | `last_collaboration_build_at` |
| 6 | Graph algorithms (`algorithms`) | `graph_algorithms.py` | `POST /api/algorithms/run` | `last_collaboration_build_at` | none (networkx) | its metric properties and `Community` nodes | `last_algorithms_run_at` |
| 7 | Embeddings (`embeddings`) | `embedding_pass.py` | `POST /api/embeddings/build` | `last_algorithms_run_at` | embeddings API, only for new or changed texts | only its chunk nodes; updates properties in place | `last_embedding_at` |

Every layer also has a `GET` endpoint (`/api/references`, `/api/knowledge`, `/api/architecture`, `/api/causal`,
`/api/collaboration`, `/api/algorithms`, `/api/embeddings`) that reads the current state without changing anything.
Missing `OPENAI_API_KEY`: Architecture, Root cause & impact and Embeddings answer 503; the Knowledge build answers 500
with the message (its route does not map the error to 503).

## 3. What each stage reads

| Stage | Reads from the graph | Needs from the data |
| --- | --- | --- |
| 1 References | text properties of the 12 scanned labels; lookups of `Issue.issue_key`, `PullRequest.(repository, pr_number)`, `Document.title` identifiers | identifiers written in text (`docs/SQL_DATA_HANDOFF.md`, section 8) |
| 2 Knowledge | per issue: the issue, its versions and comments, every node linked to them by `MENTIONS_*` in either direction, the reviews and code changes of such PRs, the versions of such documents, whole meetings of such segments; the person registry from PostgreSQL | text that names the issue; full names for actors |
| 3 Architecture | Part A: every `CodeChange.file_path`. Part B per repository: its PRs, code changes, reviews; documents linked to them by `MENTIONS_DOCUMENT`; every `technical-design` document; Slack, mail and transcript nodes that mention one of its PRs, and documents those mention | realistic file paths; messages and documents that name PRs |
| 4 Root cause & impact | per topic: its events and their `CAUSED` links, the sources the events cite (`EVIDENCED_BY`), code changes of PRs in the topic's `DERIVED_FROM`, components of those repositories, events of other topics within 30 days | an issue's PRs linked to the issue; several topics close in time |
| 5 Expertise & collaboration | person activity relationships; topic membership via `DERIVED_FROM` and `EVENT_OF_TOPIC`; component membership via files, `COMPONENT_EVIDENCED_BY`, `AFFECTED_COMPONENT`; shared issues, PRs, meetings, mails, events | the same people active across sources |
| 6 Graph algorithms | `WORKS_WITH`, `Expertise`, `DEPENDS_ON`, `AFFECTED_COMPONENT` | enough people for groups to form |
| 7 Embeddings | every node of 15 labels with its context from all layers above | text; long texts test chunking |

## 4. Staleness

`backend/pipeline_staleness.py` is the only place that decides whether a stage is stale. Every layer's GET and POST
response includes `needs_rerun` (the Knowledge layer: `needs_layer_rerun`) and `stale_reasons`, and the tab shows a
warning with each reason on its own line.

```python
UPSTREAM_BY_STAGE = {
    "references": ["import"],
    "knowledge": ["references"],
    "architecture": ["import", "references"],
    "causal": ["knowledge", "architecture"],
    "collaboration": ["import", "knowledge", "architecture", "causal"],
    "algorithms": ["knowledge", "architecture", "causal", "collaboration"],
    "embeddings": ["import", "references", "knowledge", "architecture", "causal", "collaboration", "algorithms"],
}
```

For each stage, in that order:

1. No timestamp of its own: stale, reason `Never built.`
2. Otherwise, for each upstream stage U: if U's timestamp is newer, add `<U> was rebuilt after this layer.`; if U is
   not `import` and U is stale, add `<U> is stale.`
3. Stale when at least one reason was added.

Staleness therefore travels down the whole pipeline: a new import makes every layer stale. Stage labels in reasons:
`Import`, `Reference extraction`, `Knowledge layer`, `Architecture layer`, `Root cause & impact layer`,
`Expertise & collaboration layer`, `Graph algorithms`, `Embeddings`. Tested by `backend/test_pipeline_staleness.py`.

Staleness is only a warning. Nothing stops a build on stale input, except the 409 prerequisites in section 2.

## 5. What a rebuild removes elsewhere

Layers delete their own nodes with `DETACH DELETE`, which also removes **every relationship attached to them,
including relationships other layers created**. So rebuilding one layer can strip later layers:

| Rebuilt layer | Also removed from later layers | Repair |
| --- | --- | --- |
| Knowledge (deletes `Topic`, `Event`) | `HAS_ROOT_CAUSE`, `CONTRIBUTED_TO`, `AFFECTED_COMPONENT`, `CROSS_TOPIC_CAUSED` (layer 4), `EXPERTISE_IN` to topics (layer 5), topic metrics (layer 6), topic and event vectors (layer 7); `RootCause` nodes are left without events | rebuild layers 4 to 7 |
| Architecture (deletes `Repository`, `Module`, `File`, `Component`) | `ROOT_CAUSE_IN_COMPONENT`, `AFFECTED_COMPONENT` (4), `EXPERTISE_IN` to components (5), component metrics (6), component vectors (7) | rebuild 4 to 7 |
| Root cause & impact (deletes `RootCause`) | root cause vectors (7); component `affected_event_count` becomes out of date (6) | rebuild 5 to 7 |
| Expertise & collaboration (deletes `Expertise`) | bus factor inputs (6), expert lines in texts (7) | rebuild 6 and 7 |
| Graph algorithms (deletes `Community`, clears metrics) | community vectors, metric sentences in texts (7) | rebuild 7 |

The rule is simple: **after rebuilding any layer, rebuild every layer after it, in order.** Staleness reports this.

Order of delete and write:
- **Knowledge** deletes first, then calls the model issue by issue and writes as it goes. A failed call leaves a
  partial layer; run it again.
- **Architecture** and **Root cause & impact** call the model for every repository or topic first and delete the old
  layer only when all calls succeeded. A failed call leaves the old layer untouched.
- **References**, **Expertise & collaboration**, **Graph algorithms** compute everything first, then delete and write.
- **Embeddings** never deletes other layers' data; it updates vectors in place, skips unchanged texts, and removes its
  properties from nodes that are no longer eligible.

## 6. Loading a new dataset from scratch

1. Generate SQL that satisfies `docs/SQL_DATA_HANDOFF.md` and `docs/DATA_GENERATION_GUIDE.md`.
2. Recreate the tables empty (`scripts/setup_postgres_schema.py` drops and recreates all ten; destructive) or delete
   the old rows, then insert the new rows in foreign-key order.
3. Empty the graph (the import never deletes): for example `MATCH (n) DETACH DELETE n` in Neo4j Browser. Destructive;
   the user decides. Constraints and indexes may stay.
4. Start the SQL viewer and press the import button on each of the six source pages.
5. In `Build graph layers`, run the seven tabs left to right. Layers 2, 3, 4 and 7 call OpenAI and cost money; the
   Embeddings tab has `Preview next run` to see the size first.
6. Check every tab shows no stale warning. Update the agent's test questions (`backend/ai_agent/test_questions.json`),
   whose expected names come from the old data, and run them.

## 7. How everything links: paths through the graph

Starting points are source nodes. These are the paths the layers and the agent follow.

### 7.1 Between sources

| From | To | Path |
| --- | --- | --- |
| Any source node | the people involved | source relationships (`SENT_MAIL`, `WROTE_ISSUE_COMMENT`, ...; `docs/GRAPH_DATA_HANDOFF.md` 7.1) |
| Any scanned source node | the issue, PR or document it names | `-[:MENTIONS_ISSUE|MENTIONS_PULL_REQUEST|MENTIONS_DOCUMENT]->` |
| An issue, PR or document | everything that names it | `<-[:MENTIONS_*]-` |
| An issue | its history and discussion | `-[:HAS_ISSUE_VERSION]->`, `-[:NEXT_ISSUE_VERSION]->`, `-[:HAS_ISSUE_COMMENT]->`, `-[:REPLY_TO_ISSUE_COMMENT]->` |
| A document | its versions | `-[:HAS_DOCUMENT_VERSION]->`, `-[:NEXT_DOCUMENT_VERSION]->` |
| A PR | reviews and code | `-[:HAS_PR_REVIEW]->`, `-[:REPLY_TO_PR_REVIEW]->`, `-[:HAS_CODE_CHANGE]->` |
| A meeting | what was said | `-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->`, ordered by `sequence_number` |
| A Slack reply | its thread root | `-[:SLACK_THREAD_REPLY_TO]->` |
| A mail | its parent | property `in_reply_to_id` = parent's `message_id` (no relationship) |
| Two people | shared work | `WORKS_WITH` (layer 5), or any shared source node |

Without a `MENTIONS_*` edge, two sources are connected only through a shared person.

### 7.2 From sources to interpretation

| From | To | Path |
| --- | --- | --- |
| An issue | its topic | `-[:ABOUT_TOPIC]->(Topic)` |
| A topic | every source it was built from | `-[:DERIVED_FROM]->` |
| A topic | its events | `<-[:EVENT_OF_TOPIC]-(Event)` |
| An event | the sources it cites | `-[:EVIDENCED_BY]->` |
| A source node | the events that cite it | `<-[:EVIDENCED_BY]-(Event)` |
| An event | causes and effects in its topic | `<-[:CAUSED]-`, `-[:CAUSED]->` |
| An event | causes and effects in other topics | `<-[:CROSS_TOPIC_CAUSED]-`, `-[:CROSS_TOPIC_CAUSED]->` |
| An event | its root causes | `-[:HAS_ROOT_CAUSE]->(RootCause)` |
| A root cause | where it sits and what supports it | `-[:ROOT_CAUSE_IN_COMPONENT]->(Component)`, `-[:ROOT_CAUSE_EVIDENCED_BY]->` |
| An event | code that contributed | `<-[:CONTRIBUTED_TO]-(CodeChange)` |
| An event | components it affected | `-[:AFFECTED_COMPONENT]->(Component)` |
| A person | events they acted in | `-[:ACTED_IN_EVENT]->` |

### 7.3 From code to structure

| From | To | Path |
| --- | --- | --- |
| A code change | its file, module, repository | `-[:MODIFIES_FILE]->(File)<-[:CONTAINS_FILE]-(Module)<-[:CONTAINS_MODULE]-(Repository)` |
| A file | the component implemented in it | `<-[:IMPLEMENTED_IN]-(Component)` |
| A component | its repository, dependencies, evidence | `-[:PART_OF_REPOSITORY]->`, `-[:DEPENDS_ON]->`, `<-[:DEPENDS_ON]-`, `-[:COMPONENT_EVIDENCED_BY]->` |
| A PR | the components it touched | `-[:HAS_CODE_CHANGE]->()-[:MODIFIES_FILE]->()<-[:IMPLEMENTED_IN]-(Component)` |

### 7.4 From people to knowledge and groups

| From | To | Path |
| --- | --- | --- |
| A person | what they know | `-[:HAS_EXPERTISE]->(Expertise)-[:EXPERTISE_IN]->(Topic or Component)` |
| An expertise | the activity it counts | `-[:EXPERTISE_EVIDENCED_BY]->` |
| A topic or component | its experts | `<-[:EXPERTISE_IN]-(Expertise)<-[:HAS_EXPERTISE]-(Person)` |
| A person | their group | `-[:MEMBER_OF_COMMUNITY]->(Community)` |
| A person, topic, component | metrics | properties: `collab_weighted_degree`, `collab_betweenness`, `community_id`, `bus_factor`, `expert_count`, `top_expert`, `depends_on_count`, `depended_on_by_count`, `affected_event_count` |

### 7.5 Into the graph by search

Every embedded node (label `Searchable`) is an entry point: `searchable_embedding` (vector, meaning) and
`searchable_text` (fulltext, exact words) cover the texts of all 15 labels, and each text already carries its context
from every layer. `entity_lookup` and `issue_key_lookup` resolve names and keys. A hit on an `EmbeddingChunk` leads to
its source via `-[:CHUNK_OF]->`. The agent's use of these is described in `docs/AI_AGENT_HANDOFF.md`.

## 8. Evidence identifiers shared by the LLM layers

The Knowledge, Architecture and Root cause & impact layers show the model each source node under a short identifier
and write evidence relationships only for identifiers that were in the bundle. The rules are the same everywhere,
except the `CodeChange` identifier:

| Label | Identifier | Example |
| --- | --- | --- |
| `Issue` | `issue_key` | `KV-7` |
| `IssueVersion` | `display_name` | `KV-7 v3` |
| `IssueComment` | `comment_id` | `comment-011` |
| `MailMessage` | `message_id` | `mail-008` |
| `SlackMessage` | `message_id v<version_number>` | `slack-026 v2` |
| `TeamsMeeting` | `meeting_id` | `meet-002` |
| `TeamsTranscriptSegment` | `segment_id` | `seg-025` |
| `Document` | `document_id` | `doc-001` |
| `DocumentVersion` | `display_name` | `doc-001 v2` |
| `PullRequest` | `display_name` | `kvitta-api#58` |
| `PullRequestReview` | `source_id` | `review-030` |
| `CodeChange` | Knowledge: `display_name`; Architecture and Root cause: `display_name v<version_number>` | `kvitta-api#58 app/integrations/fortnox/client.py v2` |

Derived nodes are referenced as `event:<topic_slug>/<event_slug>` and `component:<repository>/<component_slug>`
(Root cause & impact layer). Identifiers that collide across the dataset make one of the colliding items unreachable
as evidence (`docs/SQL_DATA_HANDOFF.md`, section 10).
