# Task 05 Report: Reference Extraction (deterministic)

Status: complete.

No LLM was called. No `Topic` or `Event` node was created. No chunking, embedding or index was added. No SQL schema change was made.

---

## 1. Code Change

### `backend/reference_extraction.py` (new)

The whole pass: lookups, patterns, the self-reference rule, delete-then-rebuild, and the `PipelineState` helpers. It reads and writes Neo4j only, never PostgreSQL.

Lookups are built from the graph itself rather than a hardcoded list, so whatever is in the graph is what can be referenced. Document identifiers are resolved first and their character spans consumed, so `REQ-AUTH-SESSION` cannot also be offered to the issue-key pattern; one string never produces two edges. An unresolved match is discarded, which is what removes strings such as `UTF-8`.

Each run deletes every relationship where `extracted_by = "reference-extraction-v1"` and rebuilds from scratch. Deletion is by that property only, never by relationship type, so an import-created relationship is never touched.

### `backend/app.py`

- `References` filter group with the three new relationship types.
- `GET /api/references` returns the current edges, the counts and the two timestamps, so the panel is populated on page load rather than only after a run.
- `POST /api/references/extract` runs the pass and returns the same shape.
- The `All` node query now excludes `PipelineState`, so the bookkeeping node never reaches the visualisation.
- The CORS `Access-Control-Allow-Methods` header now includes `POST`. See section 4.

### `viewer/app.py`

All six import paths write `last_import_at` to `(:PipelineState {id: "singleton"})`. This is the only change made to the import paths.

### `frontend/src/main.tsx` and `styles.css`

The "Build graph layers" tab held an unused textarea. It now holds the extraction panel: the **Extrahera referenser** button, a running state, success and failure messages, the last-extraction and last-import timestamps, the needs-re-run warning, counts per relationship type, and a full table of every edge with source label, source name, matched text, source property and target.

The button itself turns amber and carries the warning text when an import has happened since the last extraction, so the signal sits where it is acted on.

`References` was added to the frontend filter list. See section 4.

---

## 2. Result

**55 edges.** Within the expected 40–60 range.

| Relationship | Count |
| --- | --- |
| `MENTIONS_ISSUE` | 22 |
| `MENTIONS_PULL_REQUEST` | 21 |
| `MENTIONS_DOCUMENT` | 12 |
| **Total** | **55** |

Lookups built from the graph: 2 issues, 2 pull requests, 1 document identifier. 61 nodes scanned.

Graph nodes remain **68**, plus the single `PipelineState` node. Imported relationships remain **125**. No existing relationship was removed.

### All ten spot checks from section 9 are present

| Source | Matched | Target | |
| --- | --- | --- | --- |
| `SlackMessage` slack-003 | `AUTH-17` | AUTH-17 | ok |
| `TeamsTranscriptSegment` seg-001 | `AUTH-17` | AUTH-17 | ok |
| `TeamsTranscriptSegment` seg-002 | `backend-api#42` | backend-api#42 | ok |
| `DocumentVersion` doc-001 v2 | `AUTH-17` | AUTH-17 | ok |
| `DocumentVersion` doc-001 v2 | `backend-api#42` | backend-api#42 | ok |
| `IssueVersion` AUTH-19 v1 | `AUTH-17` | AUTH-17 | ok |
| `IssueVersion` AUTH-19 v1 | `backend-api#42` | backend-api#42 | ok |
| `PullRequestReview` review-001 | `REQ-AUTH-SESSION` | doc-001 | ok |
| `MailMessage` mail-003 | `AUTH-17` | AUTH-17 | ok |
| `IssueComment` comment-005 | `backend-api#42` | backend-api#42 | ok |

### Full edge table

| Source label | Source | Matched | Property | Target |
| --- | --- | --- | --- | --- |
| Document | doc-001 | `AUTH-17` | body | AUTH-17 |
| Document | doc-001 | `backend-api#42` | body | backend-api#42 |
| Document | doc-002 | `REQ-AUTH-SESSION` | body | doc-001 |
| Document | doc-002 | `AUTH-17` | body | AUTH-17 |
| Document | doc-002 | `backend-api#42` | body | backend-api#42 |
| DocumentVersion | doc-001 v1 | `AUTH-17` | body | AUTH-17 |
| DocumentVersion | doc-001 v2 | `AUTH-17` | body | AUTH-17 |
| DocumentVersion | doc-001 v2 | `backend-api#42` | body | backend-api#42 |
| DocumentVersion | doc-002 v1 | `REQ-AUTH-SESSION` | body | doc-001 |
| DocumentVersion | doc-002 v1 | `AUTH-17` | body | AUTH-17 |
| DocumentVersion | doc-002 v1 | `backend-api#42` | body | backend-api#42 |
| Issue | AUTH-17 | `backend-api#42` | description | backend-api#42 |
| Issue | AUTH-19 | `AUTH-17` | description | AUTH-17 |
| Issue | AUTH-19 | `backend-api#42` | description | backend-api#42 |
| IssueComment | comment-003 | `REQ-AUTH-SESSION` | body | doc-001 |
| IssueComment | comment-004 | `backend-api#42` | body | backend-api#42 |
| IssueComment | comment-005 | `backend-api#42` | body | backend-api#42 |
| IssueVersion | AUTH-17 v3 | `REQ-AUTH-SESSION` | description | doc-001 |
| IssueVersion | AUTH-17 v5 | `backend-api#42` | description | backend-api#42 |
| IssueVersion | AUTH-19 v1 | `AUTH-17` | description | AUTH-17 |
| IssueVersion | AUTH-19 v1 | `backend-api#42` | description | backend-api#42 |
| IssueVersion | AUTH-19 v2 | `AUTH-17` | description | AUTH-17 |
| IssueVersion | AUTH-19 v2 | `backend-api#42` | description | backend-api#42 |
| MailMessage | mail-003 | `AUTH-17` | body | AUTH-17 |
| PullRequest | backend-api#42 | `REQ-AUTH-SESSION` | description | doc-001 |
| PullRequest | backend-api#42 | `AUTH-17` | title | AUTH-17 |
| PullRequest | backend-api#47 | `AUTH-19` | title | AUTH-19 |
| PullRequest | backend-api#47 | `backend-api#42` | description | backend-api#42 |
| PullRequestReview | backend-api#42 review-001 | `REQ-AUTH-SESSION` | body | doc-001 |
| PullRequestReview | backend-api#42 review-004 | `REQ-AUTH-SESSION` | body | doc-001 |
| PullRequestReview | backend-api#47 review-006 | `backend-api#42` | body | backend-api#42 |
| SlackMessage | slack-003 | `REQ-AUTH-SESSION` | body | doc-001 |
| SlackMessage | slack-003 | `AUTH-17` | body | AUTH-17 |
| SlackMessage | slack-004 | `AUTH-17` | body | AUTH-17 |
| SlackMessage | slack-004 | `backend-api#42` | body | backend-api#42 |
| SlackMessage | slack-005 | `backend-api#42` | body | backend-api#42 |
| SlackMessage | slack-006 | `REQ-AUTH-SESSION` | body | doc-001 |
| SlackMessage | slack-006 | `REQ-AUTH-SESSION` | body | doc-001 |
| SlackMessage | slack-006 | `AUTH-17` | body | AUTH-17 |
| SlackMessage | slack-006 | `AUTH-17` | body | AUTH-17 |
| SlackMessage | slack-007 | `REQ-AUTH-SESSION` | body | doc-001 |
| SlackMessage | slack-007 | `AUTH-17` | body | AUTH-17 |
| SlackMessage | slack-008 | `AUTH-17` | body | AUTH-17 |
| SlackMessage | slack-009 | `AUTH-17` | body | AUTH-17 |
| SlackMessage | slack-009 | `backend-api#42` | body | backend-api#42 |
| SlackMessage | slack-010 | `AUTH-19` | body | AUTH-19 |
| SlackMessage | slack-010 | `backend-api#42` | body | backend-api#42 |
| SlackMessage | slack-011 | `backend-api#42` | body | backend-api#42 |
| TeamsTranscriptSegment | seg-001 | `AUTH-17` | body | AUTH-17 |
| TeamsTranscriptSegment | seg-002 | `backend-api#42` | body | backend-api#42 |
| TeamsTranscriptSegment | seg-005 | `REQ-AUTH-SESSION` | body | doc-001 |
| TeamsTranscriptSegment | seg-005 | `AUTH-17` | body | AUTH-17 |
| TeamsTranscriptSegment | seg-006 | `backend-api#42` | body | backend-api#42 |
| TeamsTranscriptSegment | seg-007 | `AUTH-17` | body | AUTH-17 |
| TeamsTranscriptSegment | seg-008 | `backend-api#42` | body | backend-api#42 |

**The four apparent duplicates on `slack-006` are not duplicates.** `slack-006` was edited, so it exists as two `SlackMessage` nodes, version 1 and version 2, both carrying the display name `slack-006`. Each node has one edge per reference. The graph key includes `version_number`; the display name does not.

### Properties on an edge

```
derived:          True
extracted_by:     reference-extraction-v1
matched_text:     AUTH-17
source_property:  body
extracted_at:     2026-09-19T22:43:07.893939+00:00
```

### Self-reference rule

| Check | Result |
| --- | --- |
| `doc-001` referencing its own document | 0 edges |
| Versions and comments of AUTH-17 referencing AUTH-17 | 0 edges |
| `AUTH-19 v1` referencing AUTH-17, a genuine cross-reference | 1 edge |

---

## 3. The two re-run checks

**Run the extraction twice.** Second run reports `deleted: 55` and creates 55. The count does not double.

```
run 1:  total 55   deleted 55   {MENTIONS_ISSUE: 22, MENTIONS_PULL_REQUEST: 21, MENTIONS_DOCUMENT: 12}
run 2:  total 55   deleted 55   {MENTIONS_ISSUE: 22, MENTIONS_PULL_REQUEST: 21, MENTIONS_DOCUMENT: 12}
```

**Run an import after an extraction.** The extracted edges survive, because the imports use `MERGE` and delete nothing.

```
derived edges before the issue import: 55
derived edges after:                   55   unchanged: True
```

The same run confirmed the needs-re-run signal works end to end:

```
last_import_at:     2026-09-19T22:43:28.584932+00:00
last_extraction_at: 2026-09-19T22:43:07.893939+00:00
needs_rerun:        True
```

---

## 4. Out of Scope

**Two changes made beyond the literal list, both required for the feature to work:**

- **`Access-Control-Allow-Methods` now includes `POST`.** It allowed only `GET, OPTIONS`. The extraction button issues a `POST` from the browser, which the browser would refuse after the preflight. The existing `/api/ai/chat` endpoint is also a `POST` and had the same latent problem. Only the method list changed; the origin allowlist is untouched.
- **`References` added to the frontend filter list and to the `DataSource` type.** The backend now returns `sourceType: "References"` for these edges, so the TypeScript type was wrong without it and the build would misrepresent the payload. Adding it to the selector is one further line and is what makes the new edges visible in the graph at all. Task 05 requires UI work, so the frontend was not off limits, but this specific addition was not asked for.

**Found and deliberately left unchanged:**

- `AGENTS.md` and `README.md` still say the schema has nine tables. It has ten. The task explicitly says not to fix them here.
- `OWNS_ISSUE`, `CREATED_ISSUE` and every other existing relationship type are untouched.
- `viewer/person_identity.py` is untouched.
- No `Topic` or `Event` node, no chunking, no embeddings, no index, not even as a placeholder.
- The reference edges point at parent entities only. Pointing a reference at a specific version would need a rule for choosing which version a bare `AUTH-17` means, and there is no such rule in the data.
- `backend/app.py` runs with `debug=True`, which is why the reloader picked up the new endpoints without a restart. Pre-existing setting, not changed.
- The `MENTIONS_*` edges deliberately carry no confidence or score. They are exact identifier matches; a score would imply an uncertainty that does not exist at this layer.
