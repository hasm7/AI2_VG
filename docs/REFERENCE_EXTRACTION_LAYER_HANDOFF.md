# Layer 1: Reference Extraction

The first graph-building step. It finds issue keys, pull request references and document identifiers written in
source text and links each mention to the node it names, with `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST` and
`MENTIONS_DOCUMENT`. Deterministic: regular expressions and graph lookups, no model.

**Verified on 2026-09-29** against `backend/reference_extraction.py`, `backend/app.py`, `frontend/src/main.tsx` and the
live graph.

Code: `backend/reference_extraction.py`. API: `backend/app.py`. UI: `ReferenceExtractionPanel` in
`frontend/src/main.tsx`, tab `Reference extraction` (first of seven in `Build graph layers`).

## 1. Why it exists

After the import, the six source trees share nothing but `Person` nodes: the only path from a mail to a pull request
runs through a person. The content links are written in the text: `KV-7` in a Slack message, `kvitta-mobile#6` in
an issue, `ADR-OFFLINE-QUEUE` in a review. This layer turns those strings into relationships. Every later layer builds
its evidence on them (`docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 3).

It interprets nothing. If a text contains an identifier and the node exists, the edge is drawn; if not, the match is
discarded. That discard is the safety net: the issue-key pattern also matches `UTF-8`, and the failed lookup drops
it.

## 2. What is scanned

Graph nodes only (never PostgreSQL). For each node, the listed text properties, and the parent entity the node must
not link back to:

| Label | Properties | Parent (self-reference check) |
| --- | --- | --- |
| `MailMessage` | `subject`, `body` | none |
| `SlackMessage` | `body` | none |
| `TeamsTranscriptSegment` | `body` | none |
| `TeamsMeeting` | `title` | none |
| `Issue` | `title`, `description` (latest version) | itself |
| `IssueVersion` | `title`, `description`, `acceptance_criteria` | its `Issue` (via `HAS_ISSUE_VERSION`) |
| `IssueComment` | `body` | its `Issue` (via `HAS_ISSUE_COMMENT`) |
| `Document` | `title`, `body` (latest version) | itself |
| `DocumentVersion` | `title`, `body`, `change_summary` | its `Document` (via `HAS_DOCUMENT_VERSION`) |
| `PullRequest` | `title`, `description` (latest version) | itself |
| `PullRequestReview` | `body` | its `PullRequest` (via `HAS_PR_REVIEW`) |
| `CodeChange` | `before_summary`, `after_summary` | its `PullRequest` (via `HAS_CODE_CHANGE`) |

Not scanned: `CodeChange.diff`, names, URLs, and the raw JSON properties (`versions_raw`, `comments_raw`,
`reviews_raw`, `code_changes_raw`, `recipients_raw`, `participants_raw`), whose content is already covered by
first-class nodes. Earlier PR versions' titles and descriptions exist only in SQL and are never scanned.

## 3. Patterns and lookups

Lookups are built from the graph at every run; nothing is hard-coded.

| Kind | Pattern | Resolves to | Lookup key |
| --- | --- | --- | --- |
| Issue | `\b[A-Z][A-Z0-9]*-\d+\b` | `Issue` | `issue_key` (any `source_instance`) |
| Pull request | `\b([a-z0-9][a-z0-9._-]*)#(\d+)\b` | `PullRequest` | `(repository, pr_number)` (any `source_instance`) |
| Document | the identifier itself, `\b<identifier>\b` | `Document` | leading token of `Document.title` |

**Document identifier.** `document_identifier(title)` takes the text before the first `:`, trims it, and keeps it only
if it matches `^[A-Z][A-Z0-9-]{3,}$`. `REQ-VAT: VAT on expenses` gives `REQ-VAT`; a title without such a prefix
(`Receipt reader`) gives nothing and that document cannot be referenced.

**Order and consumption.** For each text, document identifiers are matched first and their spans consumed; issue and
PR matches that overlap a consumed span are skipped, so `REQ-VAT` is not also tried as an issue key. Matching is
case-sensitive and whole-word.

**Self-reference rule.** A match whose target is the node's own parent (or the node itself for `Issue`, `Document`,
`PullRequest`) is skipped: a version of `KV-8` saying `KV-8` adds nothing that `HAS_ISSUE_VERSION` does not already
say. A comment on `KV-8` saying `KV-2` is kept.

**One edge per (node, type, target).** If a node names the same target several times, or in several properties, one
relationship is written with the first match's `matched_text` and `source_property`.

## 4. Graph model

```cypher
(:SourceNode)-[:MENTIONS_ISSUE]->(:Issue)
(:SourceNode)-[:MENTIONS_PULL_REQUEST]->(:PullRequest)
(:SourceNode)-[:MENTIONS_DOCUMENT]->(:Document)
```

Targets are always the parent entities (`Issue`, `PullRequest`, `Document`), never versions, reviews or code changes.

| Relationship property | Value |
| --- | --- |
| `derived` | `true` |
| `extracted_by` | `reference-extraction-v1` (this layer's marker; it does not use `generated_by`) |
| `matched_text` | the exact text matched, e.g. `KV-7` |
| `source_property` | where it was found, e.g. `body`, `description` |
| `extracted_at` | run timestamp (UTC ISO) |

No nodes are created, apart from setting `PipelineState.last_extraction_at`.

## 5. Run behaviour

`run_extraction(session)`:

1. Timestamp the run.
2. Build the three lookups from the graph (read).
3. Load every scannable node with its text properties and parent (read).
4. Collect the edges that should exist.
5. Delete every relationship with `extracted_by = "reference-extraction-v1"`.
6. Write the new relationships with `MERGE`, one type at a time.
7. Set `PipelineState.last_extraction_at`.

Idempotent: the same graph always gives the same edges. Deletion is by the `extracted_by` marker, never by type.

## 6. Current state (2026-09-29)

Kvitta data. 293 relationships, `needs_rerun: false`. The count matched an independent recomputation from the node
texts.

| Type | Count | From | Targets |
| --- | ---: | --- | --- |
| `MENTIONS_ISSUE` | 175 | SlackMessage 41, DocumentVersion 36, TeamsTranscriptSegment 28, Document 17, PullRequest 14, IssueVersion 12, MailMessage 8, Issue 7, TeamsMeeting 5, IssueComment 4, PullRequestReview 3 | all 12 issues; most `KV-8` 29, `KV-7` 24, `KV-2` 23 |
| `MENTIONS_PULL_REQUEST` | 62 | IssueVersion 17, Issue 15, SlackMessage 15, PullRequest 4, IssueComment 3, TeamsTranscriptSegment 3, Document 2, DocumentVersion 2, PullRequestReview 1 | all 14 PRs; most `kvitta-api#60` and `kvitta-mobile#6` 9 each |
| `MENTIONS_DOCUMENT` | 56 | IssueVersion 16, TeamsTranscriptSegment 9, SlackMessage 8, PullRequest 6, IssueComment 4, PullRequestReview 4, CodeChange 2, DocumentVersion 2, Issue 2, MailMessage 2, Document 1 | all 6 documents; `doc-001` and `doc-003` 14 each |

Cross-references between issues: `KV-3 -> KV-5`, `KV-8 -> KV-12`, `KV-12 -> KV-8`, and the release epic `KV-9` names
`KV-7`, `KV-8`, `KV-10` and `KV-12`. Between PRs: `kvitta-api#58 -> kvitta-api#55` (proper fix names the hotfix),
`kvitta-mobile#6 -> kvitta-api#21`, and `kvitta-api#31` and `kvitta-web#9` name each other.

Sources that name nothing (and so reach no later layer except through search or a meeting): 6 mails (`mail-001`,
`-004`, `-006`, `-007`, `-010`, `-014`: the customer's first mails, the time-report reminder, the alert and the thank
you), 13 Slack rows (small talk, thread replies and version 1 of `slack-026`), 20 transcript segments, 10 issue
comments, 26 review entries and 37 of 39 code changes.

## 7. What the data needs for this layer

- Identifiers in the exact formats of section 3, written where people would write them: PR titles starting with the
  issue key, chat lines naming the PR and issue, issue descriptions naming the PR that delivered them, reviews naming
  the requirement, meeting segments naming the issue under discussion, documents naming their tracking issue and
  implementing PR, mails to customers naming the issue.
- Document titles with an identifier prefix for every document that should be referenceable.
- Cross-references between issues (a follow-up bug naming the original story) and between PRs (a fix naming the PR
  it completes).
- Some deliberate non-matches are harmless (`UTF-8`, `ISO-8601`): they resolve to nothing and are dropped.

## 8. API

`GET /api/references`: `last_import_at`, `last_extraction_at`, `needs_rerun`, `stale_reasons`, `edges` (every
extracted edge: `source_label`, `source_display_name`, `relationship`, `matched_text`, `source_property`,
`target_display_name`, `extracted_at`), `total`, `counts` per type.

`POST /api/references/extract`: runs the pass and returns the same plus `extracted_at`, `deleted`, `scanned_nodes`,
`lookups` (`issues`, `pull_requests`, `documents`). Errors: 500 with `{"error": ...}`.

## 9. UI

Tab `Reference extraction`. Button `Extract references` / `Extracting...`; status row with the last extraction and
last import times; the stale warning with its reasons. Description (`reference-description`): `Finds issue keys, PR
numbers and document IDs in source text and links each mention to the existing node.` Counts under
`Relationships created:` (Issue, Pull request, Document, Total). Table `Extracted references (relationships:
MENTIONS_ISSUE, MENTIONS_PULL_REQUEST, MENTIONS_DOCUMENT)` with columns Source type (Label), Source (node name), Field
(property in source), Matched (text in source), Target (node name), Relationship (type). Empty state: `No references
yet. Press the button to run the pass.`

Graph filter `References`: `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST`, `MENTIONS_DOCUMENT`.

## 10. Boundaries

- Reads and writes Neo4j only; never PostgreSQL; no model.
- Creates relationships only; deletes only its own (`extracted_by`).
- Says only "this text names that entity". Meaning (implements, causes, blocks) is the Knowledge layer's job.
