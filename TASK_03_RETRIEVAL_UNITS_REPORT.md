# Task 03 Report: Retrieval Units

Status: complete. This report covers the deliverables in `TASK_03_RETRIEVAL_UNITS.md`.

No SQL schema, frontend code, chunking, embeddings, semantic extraction, `OWNS_ISSUE`, mail reply chains, transcript ordering, file nodes, commit nodes, or `PRVersion` nodes were changed.

---

## 1. Code Change

### `viewer/person_identity.py`

Added `MAILBOX_LOCAL_PARTS` and `actor_type` on `Person` clusters. Classification is exact and case-insensitive on the email local part before `@`. A cluster with no email is `"person"`, and a cluster with any human-looking email is `"person"`. It is `"mailbox"` only when every email in the cluster matches the functional mailbox pattern list.

### `viewer/app.py`

Added uniqueness constraints and import logic for five retrieval-unit labels:

| Label | Source | Unique key |
| --- | --- | --- |
| `IssueVersion` | `issue_versions` | `(source_instance, issue_id, version_number)` |
| `IssueComment` | `issue_comments` latest row per comment | `(source_instance, comment_id)` |
| `DocumentVersion` | `document_versions` | `(source_instance, document_id, version_number)` |
| `PullRequestReview` | `pr_reviews` latest row per review entry | `(source_instance, repository, pr_number, source_id)` |
| `CodeChange` | `pr_versions.code_changes` entries | `(source_instance, repository, pr_number, version_number, file_path)` |

The existing raw properties are retained: `Issue.versions_raw`, `Issue.comments_raw`, `Document.versions_raw`, `PullRequest.reviews_raw`, and `PullRequest.code_changes_raw`.

New relationships:

```cypher
(:Issue)-[:HAS_ISSUE_VERSION]->(:IssueVersion)
(:IssueVersion)-[:NEXT_ISSUE_VERSION]->(:IssueVersion)
(:Person)-[:CHANGED_ISSUE_VERSION]->(:IssueVersion)
(:Issue)-[:HAS_ISSUE_COMMENT]->(:IssueComment)
(:Person)-[:WROTE_ISSUE_COMMENT]->(:IssueComment)
(:IssueComment)-[:REPLY_TO_ISSUE_COMMENT]->(:IssueComment)
(:Document)-[:HAS_DOCUMENT_VERSION]->(:DocumentVersion)
(:DocumentVersion)-[:NEXT_DOCUMENT_VERSION]->(:DocumentVersion)
(:Person)-[:AUTHORED_DOCUMENT_VERSION]->(:DocumentVersion)
(:PullRequest)-[:HAS_PR_REVIEW]->(:PullRequestReview)
(:Person)-[:WROTE_PR_REVIEW]->(:PullRequestReview)
(:PullRequestReview)-[:REPLY_TO_PR_REVIEW]->(:PullRequestReview)
(:PullRequest)-[:HAS_CODE_CHANGE]->(:CodeChange)
```

`COMMENTED_ON_ISSUE` and `REVIEWED_PR` were left unchanged.

### `backend/app.py`

Added the new relationship types to the existing `Issues`, `Docs`, and `PRs` frontend filter groups. `Mail`, `Slack`, and `Teams` are unchanged.

---

## 2. Verification

All output below was run against the local PostgreSQL and Neo4j instances on 2026-09-19.

### 2.1 Counts before and after

Before this task:

```text
Document 2
Issue 2
MailMessage 4
Person 7
PullRequest 2
SlackMessage 12
TeamsMeeting 2
TeamsTranscriptSegment 9
Total nodes: 40
```

After imports:

```text
CodeChange 7
Document 2
DocumentVersion 3
Issue 2
IssueComment 5
IssueVersion 7
MailMessage 4
Person 7
PullRequest 2
PullRequestReview 6
SlackMessage 12
TeamsMeeting 2
TeamsTranscriptSegment 9
Total nodes: 68
Total relationships: 123
```

New nodes: 28, matching the task expectation.

### 2.2 Orphans

```text
IssueVersion 0
IssueComment 0
DocumentVersion 0
PullRequestReview 0
CodeChange 0
```

### 2.3 Version chains

AUTH-17 statuses:

```text
1 open
2 in progress
3 blocked
4 in progress
5 done
```

AUTH-17 has exactly 4 `NEXT_ISSUE_VERSION` relationships. Branch check:

```text
v1 outgoing 1 incoming 0
v2 outgoing 1 incoming 1
v3 outgoing 1 incoming 1
v4 outgoing 1 incoming 1
v5 outgoing 0 incoming 1
```

### 2.4 Author and reply edges

```text
comment-002 -> source:u-priya via WROTE_ISSUE_COMMENT
review-001 -> source:u-priya via WROTE_PR_REVIEW
comment-003 -> comment-002 via REPLY_TO_ISSUE_COMMENT
review-003 -> review-002 via REPLY_TO_PR_REVIEW
```

### 2.5 PR version representation

`backend-api#42` code changes:

```text
version 1: 2
version 2: 1
version 3: 3
```

`backend-api#42` reviews:

```text
pr_version 1: 3
pr_version 3: 2
```

The sixth review belongs to `backend-api#47`.

### 2.6 Document rewrite

Both `doc-001` versions exist as `DocumentVersion` nodes. v1 and v2 have different `body` values. v2 carries:

```text
Rewritten after the auth refinement call on 2026-03-03. Fixed expiry replaced by inactivity based expiry with an absolute ceiling, scoped to the administrator role. This change is what blocked AUTH-17.
```

### 2.7 `actor_type`

```text
email:anna.berg@example.com person
email:anna.lindqvist@example.com person
email:erik.nilsson@example.com person
email:martin.ek@northwind.example.com person
email:support@example.com mailbox
name:anna person
source:u-priya person
```

Exactly one mailbox exists: `email:support@example.com`.

### 2.8 Task 01 and Task 02 checks

Duplicate identity query returned zero rows.

`Person` count is still 7.

Anna Lindqvist reaches all seven original source labels, plus the new retrieval-unit labels:

```text
Document 1
Issue 2
MailMessage 1
PullRequest 2
SlackMessage 5
TeamsMeeting 2
TeamsTranscriptSegment 1
```

Additional new labels reached:

```text
DocumentVersion 1
IssueComment 3
IssueVersion 3
PullRequestReview 2
```

### 2.9 Idempotency

After a complete extra import run over all six sources:

```text
Before: nodes 68, relationships 123
After:  nodes 68, relationships 123
LABELS_CHANGED False
RELS_CHANGED False
```

### 2.10 Frontend and viewer

Backend graph endpoints:

```text
All    200 nodes 68 relationships 123 new_label_fallbacks 0
Mail   200 nodes 9  relationships 12  new_label_fallbacks 0
Slack  200 nodes 16 relationships 14  new_label_fallbacks 0
Teams  200 nodes 16 relationships 26  new_label_fallbacks 0
Issues 200 nodes 18 relationships 36  new_label_fallbacks 0
Docs   200 nodes 7  relationships 9   new_label_fallbacks 0
PRs    200 nodes 18 relationships 26  new_label_fallbacks 0
```

SQL viewer pages:

```text
mail detail 200
slack detail 200
teams detail 200
issues detail 200
documents detail 200
prs detail 200
mail table 200
slack table 200
teams table 200
issues table 200
documents table 200
prs table 200
```

### 2.11 Constraints

New constraints confirmed:

```text
code_change_key: CodeChange(source_instance, repository, pr_number, version_number, file_path)
document_version_key: DocumentVersion(source_instance, document_id, version_number)
issue_comment_key: IssueComment(source_instance, comment_id)
issue_version_key: IssueVersion(source_instance, issue_id, version_number)
pull_request_review_key: PullRequestReview(source_instance, repository, pr_number, source_id)
```

---

## 3. Documentation Updated

### `docs/GRAPH_DATA_HANDOFF.md`

Updated import flow, constraints, relationship filter table, `Person.actor_type`, functional mailbox rule, all five new retrieval-unit models, and the "What the Graph Does Not Model Yet" section. The document now states that raw JSON properties are retained alongside first-class retrieval-unit nodes.

### `docs/SQL_DATA_HANDOFF.md`

No SQL model changed. Added only generation guidance for functional mailbox email local parts and the resulting `actor_type = "mailbox"` graph classification.

---

## 4. Out of Scope

Found and deliberately left unchanged:

- `OWNS_ISSUE` still follows the assignee fallback rule and does not separately connect the creator.
- `Issue.versions_raw`, `Issue.comments_raw`, `Document.versions_raw`, `PullRequest.reviews_raw`, and `PullRequest.code_changes_raw` remain on parent nodes.
- No `PRVersion` node was added.
- No chunking, embeddings, vector index, or fulltext index was added.
- No cross-source semantic links were extracted.
- Mail reply chains remain properties, not relationships.
- Transcript segment order remains `sequence_number`, not `NEXT_SEGMENT`.
- Files and commits were not promoted to nodes.
- The frontend was not changed.
- `AGENTS.md` and `README.md` still say nine tables rather than ten. The task asked for both handoff documents; these two stale files remain a known gap from Task 02.

---

## Files Changed

| File | Change |
| --- | --- |
| `viewer/person_identity.py` | Added functional mailbox pattern list and `Person.actor_type`. |
| `viewer/app.py` | Added retrieval-unit constraints, nodes, and relationships while retaining raw properties and existing parent relationships. |
| `backend/app.py` | Added new relationship types to existing source filter groups. |
| `docs/GRAPH_DATA_HANDOFF.md` | Documented new labels, constraints, properties, relationships, filters, and remaining gaps. |
| `docs/SQL_DATA_HANDOFF.md` | Added functional mailbox generation guidance. |
