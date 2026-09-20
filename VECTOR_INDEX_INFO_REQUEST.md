# Information Request: Vector Index Planning

**Purpose.** Decide exactly which vector indexes to create for Graph RAG over this Neo4j graph, without guessing. `GRAPH_SCHEMA_HANDOFF.md` gives labels, properties and types, but not the facts below. Each section states the question, how to answer it, and what decision it drives.

Answer every section. Where the test dataset is too small to be meaningful, say so explicitly rather than reporting a number that cannot be trusted — the fixture is 2–12 nodes per label.

---

## 1. Text volume and length per candidate property

**Question.** For each property below: how many nodes have it non-null and non-empty, and what is the character length distribution (min / median / p90 / max)?

| Label | Property |
| --- | --- |
| `MailMessage` | `subject`, `body` |
| `SlackMessage` | `body` |
| `TeamsTranscriptSegment` | `body` |
| `IssueVersion` | `title`, `description`, `acceptance_criteria` |
| `IssueComment` | `body` |
| `DocumentVersion` | `title`, `body` |
| `PullRequestReview` | `body` |
| `CodeChange` | `diff`, `before_summary`, `after_summary` |
| `Topic` | `name`, `summary` |
| `Event` | `name`, `summary` |

**How.**

```cypher
MATCH (n:DocumentVersion)
WHERE n.body IS NOT NULL AND n.body <> ''
RETURN count(n) AS n_nonempty,
       min(size(n.body)) AS min_len,
       percentileCont(size(n.body), 0.5) AS p50,
       percentileCont(size(n.body), 0.9) AS p90,
       max(size(n.body)) AS max_len;
```

Also report the total node count per label so the empty rate can be computed.

**Decides.** Whether a property needs chunking before embedding (anything with p90 above roughly 6 000 characters does), and whether a property is too short to be worth embedding at all (median under roughly 80 characters means it belongs in a fulltext index, not a vector index).

**If production volume will differ materially from the fixture, give the expected production magnitude per label** (order of magnitude is enough: hundreds / tens of thousands / millions).

---

## 2. Duplication between parent nodes and version nodes

**Question.** Is the text on `Issue`, `Document` and `PullRequest` byte-identical to the corresponding latest-version node, or does it diverge?

**How.**

```cypher
MATCH (i:Issue)-[:HAS_ISSUE_VERSION]->(v:IssueVersion)
WITH i, v ORDER BY v.version_number DESC
WITH i, collect(v)[0] AS latest
RETURN count(*) AS total,
       sum(CASE WHEN i.description = latest.description THEN 1 ELSE 0 END) AS identical_description,
       sum(CASE WHEN i.title = latest.title THEN 1 ELSE 0 END) AS identical_title;
```

Repeat for `Document`/`DocumentVersion` (`body`, `title`) and `PullRequest`/`PullRequestReview` is not applicable — for `PullRequest`, state whether `description` is a copy of the latest `pr_versions` row.

**Decides.** Whether indexing both levels would return duplicate hits. If identical, only the version node gets an embedding.

---

## 3. Version churn inside `IssueVersion` and `DocumentVersion`

**Question.** How much does the text actually change between consecutive versions of the same issue or document?

**How.** For each pair linked by `NEXT_ISSUE_VERSION` / `NEXT_DOCUMENT_VERSION`, report the share of pairs where the text field is unchanged, and for the changed ones, a rough magnitude of change (character-length delta, or a similarity ratio from `difflib.SequenceMatcher`).

**Decides.** Whether to embed every version or only the latest. High churn (most versions genuinely differ) means all versions are worth indexing for historical questions. Low churn means near-duplicate clusters will dominate every search result, and only the latest version should be embedded.

---

## 4. Coverage of the derived `Topic` / `Event` layer

**This is the most important section.** The retrieval strategy depends on it.

**Question.** What fraction of source content is reachable from the derived layer?

**How.**

```cypher
// Source nodes with at least one inbound EVIDENCED_BY or DERIVED_FROM
MATCH (n)
WHERE any(l IN labels(n) WHERE l IN [
  'MailMessage','SlackMessage','TeamsTranscriptSegment','IssueVersion',
  'IssueComment','DocumentVersion','PullRequestReview','CodeChange'])
OPTIONAL MATCH (d)-[:EVIDENCED_BY|DERIVED_FROM]->(n)
WITH labels(n)[0] AS label, n, count(d) AS inbound
RETURN label, count(n) AS total,
       sum(CASE WHEN inbound > 0 THEN 1 ELSE 0 END) AS covered;
```

Also answer:

- How many `Topic` and `Event` nodes exist per 100 source nodes, at fixture scale and expected at production scale?
- Is the extraction pass run over the full corpus, or only over a subset (a time window, one source group, a sample)?
- How long is an `Event.summary` and a `Topic.summary` in practice (covered by section 1, but confirm whether the generating prompt constrains length)?
- Does `topic-event-extraction-v1` produce stable slugs across reruns, or do `Topic.slug` / `Event.slug` change when the pass is re-run on unchanged input?

**Decides.** If coverage is high (most source nodes are evidenced by some event), retrieval can enter at the derived layer and traverse down, and source-level vector indexes become a fallback. If coverage is low or patchy, source-level vector indexes are the primary retrieval path and the derived layer is only an enrichment. The slug stability answer decides whether embeddings on derived nodes survive a rerun or must be recomputed every time.

---

## 5. Language and content character

**Question.**

- What natural language is the content in — Swedish, English, or mixed? Report the split per label if mixed.
- Do `body` fields contain markup (Markdown, HTML, quoted reply chains, Slack mrkdwn, signature blocks)? Give two or three representative raw samples per label, verbatim, truncated to 500 characters.
- For `PullRequestReview.body` specifically: what share are trivial ("LGTM", "+1", a single emoji) versus substantive review reasoning?

**Decides.** The embedding model choice (multilingual or not), the fulltext analyzer choice, and whether a cleaning step is needed before embedding. It also decides whether `PullRequestReview` earns a vector index or should be reached only by traversal.

---

## 6. Embedding pipeline constraints

**Question.**

- Which embedding model is intended or already in use? State provider, model name, output dimension, and max input tokens.
- Is there a cost or latency budget that caps how many nodes can be embedded, or how often re-embedding may run?
- Where would the embedding step run — inside `viewer/app.py` at import time, as a separate pass like `backend/reference_extraction.py`, or on demand?
- Are imports incremental or full-refresh? If `MERGE` rewrites a node's properties on rerun, does it overwrite or preserve an `embedding` property written by a later pass?

**Decides.** Index `vector.dimensions` and `vector.similarity_function`, whether chunking is required by token limit, and whether embeddings need a separate write path to avoid being clobbered by re-import.

---

## 7. Query catalogue

**Question.** List 15–25 real questions the AI layer is expected to answer, as a user would phrase them. Include the hard ones, not just the easy ones. For each, note whether the expected answer is a fact, a list, a timeline, a causal explanation, or a summary.

If a set of target questions already exists in `backend/langgraph_agent/agent.py`, its prompts, or any test fixture, extract it from there rather than inventing it.

**Decides.** Which layer each question enters at, and therefore which indexes actually get used. An index nothing queries is a liability.

---

## 8. Existing retrieval behaviour

**Question.** What does `backend/langgraph_agent/agent.py` do today to find nodes — generated Cypher, fixed query templates, keyword matching, something else? Include the tool or function definitions it exposes to the model.

Also: does `backend/app.py` expose any search endpoint the frontend uses, and how does it locate nodes?

**Decides.** Whether vector search replaces the current entry-point mechanism or supplements it, and what interface the new indexes must be reachable through.

---

## 9. Environment

**Question.**

- Neo4j version and edition (`CALL dbms.components()`).
- Deployment: Aura, Docker, self-hosted.
- Output of `SHOW INDEXES` and `SHOW CONSTRAINTS` as of now, verbatim.
- Available heap and page cache, if self-hosted.

**Decides.** Whether vector indexes are supported at all, whether quantization and hybrid search are available, and what index configuration options exist in this version.

---

## Output format

Answer inline under each numbered section. Raw query output is preferred over prose summaries. Where a question cannot be answered from the current data, write `UNKNOWN` plus the reason — do not estimate.
