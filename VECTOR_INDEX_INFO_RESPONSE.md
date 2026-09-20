# Vector Index Planning — Answers

Answers to `VECTOR_INDEX_INFO_REQUEST.md`, gathered by read-only Cypher queries against the live database and by reading `backend/langgraph_agent/agent.py`, `backend/app.py`, `backend/topic_event_extraction.py`, `viewer/app.py`, and `requirements.txt`. Nothing was estimated; anything not answerable from the current state is marked `UNKNOWN`.

The fixture is 2–12 nodes per label, as the request warned. Every number below is reported as-is, not scaled up — read distributions as *shape*, not as *magnitude*.

---

## 1. Text volume and length per candidate property

All counts are `total_nodes` from the label; `n_nonempty` is how many have the property set and non-empty. Lengths are characters.

| Label | Property | total | non-empty | min | p50 | p90 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `MailMessage` | `subject` | 4 | 4 | 46 | 50 | 51 | 51 |
| `MailMessage` | `body` | 4 | 4 | 299 | 350 | 483 | 519 |
| `SlackMessage` | `body` | 12 | 12 | 102 | 149 | 191 | 265 |
| `TeamsTranscriptSegment` | `body` | 9 | 9 | 104 | 158 | 216 | 354 |
| `IssueVersion` | `title` | 7 | 7 | 57 | 61 | 64 | 64 |
| `IssueVersion` | `description` | 7 | 7 | 172 | 334 | 397 | 444 |
| `IssueVersion` | `acceptance_criteria` | 7 | 7 | 64 | 113 | 259 | 259 |
| `IssueComment` | `body` | 5 | 5 | 97 | 163 | 216 | 242 |
| `DocumentVersion` | `title` | 3 | 3 | 20 | 48 | 48 | 48 |
| `DocumentVersion` | `body` | 3 | 3 | 439 | 769 | 1042 | 1110 |
| `PullRequestReview` | `body` | 6 | 6 | 73 | 107 | 201 | 269 |
| `CodeChange` | `diff` | 7 | 7 | 54 | 99 | 184 | 213 |
| `CodeChange` | `before_summary` | 7 | 7 | 33 | 62 | 76 | 89 |
| `CodeChange` | `after_summary` | 7 | 7 | 45 | 79 | 140 | 151 |
| `Topic` | `name` | 1 | 1 | 37 | 37 | 37 | 37 |
| `Topic` | `summary` | 1 | 1 | 163 | 163 | 163 | 163 |
| `Event` | `name` | 10 | 10 | 38 | 47 | 68 | 70 |
| `Event` | `summary` | 10 | 10 | 134 | 206 | 300 | 347 |

Reading against the request's own thresholds: **nothing here needs chunking** — max observed length is 1110 characters (`DocumentVersion.body`), far under the ~6000-character threshold where chunking would become necessary. **Nothing here is too short to embed** either — every property's median is well above 80 characters, including the shortest (`MailMessage.subject`, median 50). So on text length alone, every property in the request table is a plausible single-vector-per-node candidate, with `DocumentVersion.body` and `IssueVersion.description` the longest and most information-dense.

Empty rate is 0% everywhere in this fixture — every node of every label has every listed property populated. That is a property of the fixture (`AGENTS.md` tells data generators to fill these fields), not a guarantee; the SQL schema marks several of the source columns as optional (see `docs/SQL_DATA_HANDOFF.md`), so production data should not be assumed to be 100% populated.

**Production magnitude: `UNKNOWN`.** No target scale is documented anywhere in the repo (`AGENTS.md`, `docs/SQL_DATA_HANDOFF.md`, `docs/GRAPH_DATA_HANDOFF.md`) — this is simulated data with no stated production target. This needs a decision from whoever owns the roadmap, not a measurement.

---

## 2. Duplication between parent nodes and version nodes

| Pair | total | identical text | identical title |
| --- | --- | --- | --- |
| `Issue` vs. latest `IssueVersion` (`description`) | 2 | 2/2 | 2/2 (`title`) |
| `Document` vs. latest `DocumentVersion` (`body`) | 2 | 2/2 | 2/2 (`title`) |

Both `Issue` and `Document` are **fully duplicate** of their latest version node in this fixture — every field checked matches byte-for-byte. This is expected from the import logic in `viewer/app.py`: `Issue.description`/`title` and `Document.body`/`title` are explicitly copied from "latest `issue_versions`"/"latest `document_versions`" row (see `docs/GRAPH_DATA_HANDOFF.md`, Properties tables). It is not a data coincidence, it is how the importer writes both nodes.

**Decision this supports directly:** embedding both `Issue`/`Document` and their latest version node would return the same hit twice under a different node id. Only the version-level node (`IssueVersion`, `DocumentVersion`) should carry the embedding; the parent node (`Issue`, `Document`) should be reached by traversal (`HAS_ISSUE_VERSION`/`HAS_DOCUMENT_VERSION`) from the version hit, not by its own vector index.

`PullRequest.description`: the property exists on both nodes checked (`with_description: 2/2`), but `PullRequestReview` has no `description` field to compare against — a PR's description isn't duplicated onto a review the way `Issue`/`Document` duplicate onto their version nodes (the model is structurally different: `PullRequestReview` rows are review *entries* attached to a PR, not sequential whole-PR snapshots). `PullRequest.description` is closer to the "latest pr_versions row" case documented for `Issue`/`Document`, i.e. it is *also* a copy of the latest version's field, but there is no separate `PullRequestVersion` node type to deduplicate against (only `PullRequest` itself carries this text; `code_changes_raw`/`reviews_raw` are the version-shaped children, and `CodeChange`/`PullRequestReview` are their first-class node forms). So `PullRequest.description`/`.title` is not a duplication problem — it's the only place that text exists as a first-class node property, and embedding it directly is correct.

---

## 3. Version churn inside `IssueVersion` and `DocumentVersion`

### `IssueVersion` (5 `NEXT_ISSUE_VERSION` pairs across 2 issues)

| unchanged `description` | unchanged `title` | unchanged `status` |
| --- | --- | --- |
| 1/5 | 4/5 | 0/5 |

Per-pair detail (`issue`, `v→v`, description length before/after):

| Issue | v(from)→v(to) | len before | len after |
| --- | --- | --- | --- |
| issue-001 | 1→2 | 261 | 261 (unchanged) |
| issue-001 | 2→3 | 261 | 357 |
| issue-001 | 3→4 | 357 | 365 |
| issue-001 | 4→5 | 365 | 172 |
| issue-002 | 1→2 | 334 | 444 |

`status` changes on every single transition — that is the point of the issue lifecycle model (`docs/SQL_DATA_HANDOFF.md`: "Issues are the only object in the data model with a lifecycle"). `description` changes on 4 of 5 transitions, often substantially (172↔365 chars, i.e. shrinking to a third of its length on one transition — a rewrite, not an edit). `title` is comparatively stable (unchanged on 4 of 5).

**Reading:** this is high churn. Consecutive `IssueVersion.description` values are not near-duplicates of each other; they represent materially different states of the ticket. **Every `IssueVersion` should be embedded individually**, not only the latest — a question like "why did the AUTH-17 scope change" needs the earlier description in the index, not just the current one.

### `DocumentVersion` (1 `NEXT_DOCUMENT_VERSION` pair)

| unchanged `body` | unchanged `title` |
| --- | --- |
| 0/1 | 1/1 |

The one observed transition (`doc-001`, v1→v2) grew `body` from 439 to 1110 characters — again a substantive rewrite, not a copy-edit, and `title` held steady. Same reading as `IssueVersion`: **embed every `DocumentVersion`**, not only the latest.

Sample size is 1 pair for documents, so this is directional rather than statistically established — but it points the same way as the issue data, and nothing in the generation rules (`docs/SQL_DATA_HANDOFF.md`) suggests documents would churn differently in production.

---

## 4. Coverage of the derived `Topic` / `Event` layer

| Label | total | covered (inbound `EVIDENCED_BY`/`DERIVED_FROM`) | coverage |
| --- | --- | --- | --- |
| `MailMessage` | 4 | 1 | 25% |
| `SlackMessage` | 12 | 8 | 67% |
| `TeamsTranscriptSegment` | 9 | 9 | 100% |
| `IssueVersion` | 7 | 7 | 100% |
| `IssueComment` | 5 | 5 | 100% |
| `DocumentVersion` | 3 | 3 | 100% |
| `PullRequestReview` | 6 | 6 | 100% |
| `CodeChange` | 7 | 4 | 57% |
| **Total** | **53** | **43** | **81%** |

`Topic` count: 1. `Event` count: 10. Per 100 source nodes (of the 53 checked above): ~1.9 topics, ~18.9 events. **This ratio is not meaningful at this scale** — it is one topic absorbing effectively the entire fixture, because the fixture is one storyline (`AUTH-17`/`AUTH-19`, the session-expiry saga, end to end). Do not extrapolate a "topics per 100 nodes" density from a single-topic dataset.

**Is the extraction pass run over the full corpus, or a subset?** Full corpus of the topic candidate type. `backend/topic_event_extraction.py` runs one OpenAI call per `Issue` (docstring and code confirm: "For each issue, a bundle is assembled..."), and there are 2 `Issue` nodes in this database, matching 2 calls. Both issues resolved to the same `Topic` (`existing_topic_slug` matched on the second call), which is why 1 `Topic` node exists despite 2 source issues. **`PullRequest` and `Document` are not topic candidates in their own right** — they only enter the graph as evidence pulled into an issue's bundle via the Task 05 reference layer or sibling/meeting expansion. This matches the caveat already recorded in `docs/GRAPH_DATA_HANDOFF.md` under "What the Graph Does Not Model Yet."

**Does length confirm a prompt-enforced cap?** No. `EXTRACTION_INSTRUCTIONS` in `backend/topic_event_extraction.py` (lines 102–124) says "Prefer few well-evidenced events over many thin ones" but imposes no character or token limit on `summary`. The lengths in section 1 (`Event.summary` median 206, max 347; `Topic.summary` 163) are an emergent effect of the prompt's instruction to be selective, not an enforced ceiling. Do not assume a hard cap for capacity planning.

**Slug stability across reruns: unstable, by design.** `Topic.slug` and `Event.slug` are free-form strings the model invents per call (`TopicOut.slug: str`, `EventOut.slug: str` in the Pydantic schema — no `slugify(title)` or hash-based derivation anywhere in the file). Cross-call consistency *within one run* is enforced only through `existing_topic_slug`, which the code checks against topics "resolved so far in the current run," explicitly **not** read back from Neo4j, because — quoting the module docstring / code comment directly — "prior runs of this pass are deleted at the start of every run." Every rebuild (`run_extraction`'s counterpart for this layer) deletes every node/relationship with `generated_by = "topic-event-extraction-v1"` and starts over, so there is no mechanism that would make the model reproduce the same slug string on a second run over unchanged input.

**Decision this supports directly:** an `embedding` property on `Topic`/`Event` nodes cannot be assumed to survive a knowledge-layer rebuild, because the node it was attached to may not exist under the same key afterward — the old node is deleted, not merged forward. Any embedding pipeline for the derived layer must either (a) re-embed on every rebuild as a step chained after `build_knowledge_layer`, or (b) the extraction pass itself must be changed to derive a stable slug (e.g. from `topic_type` + a normalized name, or from the issue key for topics) before embeddings on this layer are worth the write cost. As-is, coverage is high (81% of the checked source labels have some derived-layer connection) but the *nodes carrying that connection* are not stable across time, which argues for entering retrieval at the source layer (`IssueVersion`, `DocumentVersion`, etc., which have stable merge keys) and treating `Topic`/`Event` as enrichment rather than a stable index target, until slug generation is made deterministic.

---

## 5. Language and content character

**Language.** English, uniformly, across every sample pulled from every label (`MailMessage`, `SlackMessage`, `TeamsTranscriptSegment`, `IssueVersion`, `DocumentVersion`, `PullRequestReview` — see raw samples below). No Swedish content was found in any source-of-truth field. Note this is despite `backend/langgraph_agent/agent.py`'s system prompt instructing the chat agent to *answer* in Swedish (`AgentState` / `call_openai`) — that is a UI-response-language convention layered on top of English source data, not a property of the corpus itself. Report split: 100% English / 0% Swedish across all sampled nodes.

**Markup.** `DocumentVersion.body` is genuine Markdown (`#`/`##` headers, structured sections like "## Background", "## Requirement", "## Out of scope") — a fulltext or embedding pipeline should expect and probably strip Markdown syntax for `DocumentVersion`. `MailMessage.body` has simple sign-off blocks ("Best regards, Anna Berg" / plain first-name sign-offs) but **no quoted reply chains** (no `>`-prefixed quoted text, no "On [date], X wrote:" boilerplate) — replies are new prose, not client-style quote-and-reply. `SlackMessage.body` and `TeamsTranscriptSegment.body` are plain prose with no markup at all in the samples pulled (no Slack `mrkdwn` like `*bold*`/`` `code` ``/`<@user>` observed). `CodeChange.diff` is a **literal `\n`-escaped string**, not real newlines (see raw sample below — the diff text contains the two characters `\` and `n`, not an actual line break). That's worth flagging as a data-shape note for anyone embedding or displaying it: it needs unescaping before it reads as a diff.

**`PullRequestReview.body` triviality.** All 6 review bodies in the fixture are substantive review reasoning — length range 73–269 characters, none is a bare "LGTM"/"+1"/emoji-only comment. **0% trivial in this sample.** Caveat: 6 is a very small sample to generalize a triviality rate from; production PR review data typically does include a meaningful share of trivial approvals, and this fixture simply wasn't generated with any.

Raw samples (verbatim, truncated to 500 characters, one label shown per row):

| Label.property | Sample |
| --- | --- |
| `MailMessage.body` | "Hello Martin,\n\nThis shipped yesterday. It took longer than we first estimated, because our initial fix simply extended the fixed expiry to sixty minutes and our security review rejected that approach. The behaviour is now based on inactivity rather than a fixed window, with an absolute cap of eight hours, and it applies to the administrator role only.\n\nIn practice an export that keeps the session active will no longer be interrupted.\n\nThe work is tracked as AUTH-17 if you need a reference.\n\nBest" |
| `SlackMessage.body` | "AUTH-17 is open. Acceptance criteria for now: administrator sessions expire after sixty minutes of inactivity. I will write it up properly in REQ-AUTH-SESSION." |
| `TeamsTranscriptSegment.body` | "Then I have to stop this here. A fixed sixty minute window fails the session policy the same way thirty does. The policy requires expiry on inactivity, not on elapsed time, and it requires an absolute ceiling regardless of activity. Eight hours is what we use elsewhere. It should also be scoped to the administrator role rather than applied to everyone." |
| `DocumentVersion.body` | "# REQ-AUTH-SESSION: Administrator session lifetime\n\n## Background\n\nNorthwind reported on 2026-02-24 that administrator accounts are signed out during audit exports that run longer than thirty minutes. The current expiry is a fixed constant applied to all roles.\n\n## Requirement\n\nAdministrator sessions expire after sixty minutes of inactivity.\n\n## Out of scope\n\nNon administrator roles. Client side session handling.\n\n## Tracking\n\nAUTH-17." |
| `PullRequestReview.body` | "Requesting changes as agreed in the refinement call. A fixed window fails the session policy whatever the value. This needs inactivity based expiry, an absolute ceiling of eight hours, and scoping to the administrator role. See REQ-AUTH-SESSION v2 once it is published." |
| `CodeChange.diff` | `@@ -12,7 +12,7 @@\n-SESSION_EXPIRY_MINUTES = 30\n+SESSION_EXPIRY_MINUTES = 60` (literal backslash-n, not a real newline) |

---

## 6. Embedding pipeline constraints

**Embedding model: `UNKNOWN` — nothing is intended or in use yet.** `requirements.txt` has no embedding-capable package beyond the general-purpose `openai>=1.99` SDK (which *can* call an embeddings endpoint, but nothing in the codebase does). There is no vector store client, no `numpy`/`scikit-learn`, no chunking library. This is a greenfield decision, not a documented one. The two OpenAI models already in use elsewhere are unrelated to embeddings: `gpt-4.1-mini` (chat agent, `backend/langgraph_agent/agent.py`) and `gpt-5.6-terra` (Task 06 structured extraction, `backend/topic_event_extraction.py`) are both generation models.

**Cost/latency budget: `UNKNOWN`.** No budget, rate limit, or re-embedding cadence is documented anywhere in the repo.

**Where would embedding run?** No existing hook. The two precedents in this codebase for "a pass that writes derived state to Neo4j on demand, not during import" are `backend/reference_extraction.py` (Task 05, triggered from a frontend button, idempotent via a stamped property) and `backend/topic_event_extraction.py` (Task 06, same pattern, own stamped property). An embedding pass should follow the same shape — its own `generated_by`-equivalent marker, triggered on demand from the frontend's "Build graph layers" tab, not folded into `viewer/app.py`'s import path — rather than running at import time.

**Does re-import clobber a later `embedding` property?** No. Every import write in `viewer/app.py` uses `MERGE (node {key...}) SET node.field1 = $x, node.field2 = $y, ...` with an explicit, closed list of fields (verified directly, e.g. `MailMessage` import at `viewer/app.py:493-507`). None of the import paths use a wildcard overwrite (`SET n = $map` or `SET n += $map`). A property not named in that `SET` clause — such as a future `embedding` — is left untouched by a re-import. **An embedding pass can write `node.embedding` safely without a re-import ever erasing it**, and does not need to run after every import; it only needs to run when the underlying text property actually changed.

---

## 7. Query catalogue

**`UNKNOWN` — no catalogue exists.** `backend/langgraph_agent/agent.py` is a single-node LangGraph graph that does exactly one thing: takes the raw user message, sends it to `client.responses.create()` with a two-line system prompt ("You are a concise assistant inside a software-team graph app. Answer in Swedish unless the user asks for another language.") and returns `response.output_text`. It defines no tools, no function-calling schema, no Cypher generation, and reads nothing from Neo4j or Postgres — the model currently answers from its own knowledge plus whatever is in the single user message, with zero grounding in this project's data. There are also no test fixtures, prompt files, or example-question lists anywhere in the repo (`backend/`, `docs/`, `viewer/`) that could be read as an implicit query catalogue.

This means the query catalogue has to be written by whoever is designing the Graph RAG layer — it is not recoverable from existing code, and this request cannot manufacture one without guessing, which the request explicitly says not to do.

---

## 8. Existing retrieval behaviour

**`backend/langgraph_agent/agent.py`:** none. As described in section 7, it is a stateless pass-through to `OpenAI().responses.create()`. No generated Cypher, no fixed templates, no keyword matching, no tool/function definitions of any kind — `AgentState` carries only `message` and `answer`, and the single graph node (`call_openai`) is the entire pipeline.

**`backend/app.py`:** one relevant endpoint, `GET /api/graph?source=<filter>` (`load_neo4j_graph`, lines 237–337). It is not a search endpoint — it takes a coarse `source` filter from a fixed enum (`Mail`, `Slack`, `Teams`, `Issues`, `Docs`, `PRs`, `References`, `Knowledge`, or `All`) mapped to a hardcoded relationship-type allowlist (`GRAPH_SOURCE_RELATIONSHIPS`), and returns *every* node/relationship reached by that relationship-type set, unfiltered by any query term. There is no `/api/search`, no free-text lookup, no node-by-id fetch endpoint, and no pagination — the frontend's graph view is a full pull, not a query. `/api/references` and `/api/knowledge` similarly return their whole layer's current state, not a filtered search result.

**Decision this supports directly:** vector search would not be replacing an existing entry point — there isn't one. It would be net-new: the first mechanism in this codebase for finding specific nodes by meaning or keyword rather than by fixed relationship-type filter or by exact constraint-key lookup.

---

## 9. Environment

| Component | Value |
| --- | --- |
| Neo4j Kernel version | `2026.08.1` |
| Neo4j edition | `enterprise` |
| Cypher versions | `5`, `25` |
| Connection URI (`.env`) | `neo4j://127.0.0.1:7687` |

**Deployment: self-hosted, local.** The URI resolves to `127.0.0.1` and `backend/app.py`'s `neo4j_connection_settings()` explicitly rewrites `neo4j://127.0.0.1:7687` / `neo4j://localhost:7687` to `bolt://` — a pattern that only makes sense against a single local instance, not an Aura cluster (Aura URIs are `neo4j+s://<id>.databases.neo4j.io`, never localhost) and not a routing-aware Docker Compose cluster setup. This is edition `enterprise` running locally, not a hosted product tier.

**Enterprise edition matters for the actual decision this section is meant to support:** Neo4j's vector index feature (`db.index.vector.createNodeIndex` / `CREATE VECTOR INDEX`) is available on both Community and Enterprise from 5.13+ and this instance is well past that (kernel `2026.08.1`), so **vector indexes are supported**. Enterprise additionally unlocks quantization options for vector indexes (`vector.quantization.enabled`) that Community does not have, so quantized/compressed vector storage is on the table here, not blocked by edition.

`SHOW INDEXES` / `SHOW CONSTRAINTS`: verbatim output is already recorded in `GRAPH_SCHEMA_HANDOFF.md` section 2 — repeating it here would just be copy-paste, so it isn't duplicated in this file. Summary: 15 uniqueness constraints (one per source/derived node label's merge key), each with its own backing `RANGE` index, plus Neo4j's two automatic label/relationship-type token lookup indexes. **No vector index, no fulltext index, and no index on any non-key property exists yet.**

**Available heap and page cache: `UNKNOWN`.** This is a local instance outside the project directory (no `neo4j.conf`, `docker-compose.yml`, or equivalent deployment file exists anywhere in this repository — confirmed by search), so its memory configuration is not visible from the codebase. Answering this needs either direct access to the Neo4j installation's `conf/neo4j.conf` on this machine, or `CALL dbms.listConfig()` run against an admin-privileged session (the current read-only introspection connects as the `neo4j` user but `dbms.listConfig` needs the `dbms.listConfig` procedure permission, which was not attempted here since it can return configuration a security-conscious setup may not want auto-collected — flag this back if you want it collected explicitly).

---

## Summary of decisions this unblocks

- **Chunking:** not needed anywhere in this schema — nothing approaches the length where it would matter.
- **Level to embed:** `IssueVersion` and `DocumentVersion`, not `Issue`/`Document` (duplicate text — section 2) and not just the latest version (real churn between versions — section 3).
- **Derived layer (`Topic`/`Event`):** high content coverage but unstable node identity across rebuilds (non-deterministic slugs) — treat as enrichment/traversal target, not a primary embedding target, unless slug generation is made deterministic first.
- **Language/cleanup:** English-only in this fixture; strip Markdown headers before embedding `DocumentVersion.body`; unescape the literal `\n` in `CodeChange.diff` before embedding or displaying it.
- **Embedding pipeline shape:** a new on-demand pass mirroring `reference_extraction.py`/`topic_event_extraction.py`, writing `embedding` properties that re-import will not clobber; model/budget/cadence are undecided and need a product decision, not more data-gathering.
- **Retrieval integration:** there is no existing search surface to slot into — this is the first one, and `backend/langgraph_agent/agent.py` currently does no retrieval at all, so wiring vector search into it is also net-new scope, not a modification of something that already partially works.
- **Platform:** Enterprise edition, current version, local — vector indexes with optional quantization are available now, no upgrade or edition change required.
