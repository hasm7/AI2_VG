# Task 01 Report: Person Identity Resolution

Status: complete. This report covers the three deliverables in section 7 of `TASK_01_PERSON_IDENTITY.md`.

Nothing outside the declared scope was changed. No DDL, no other node label, no change to which properties are copied onto other nodes, no new or renamed relationship types, and no change to `backend/app.py` or the frontend.

---

## 1. Code Change

### New module: `viewer/person_identity.py`

Identity resolution runs as an explicit two-phase step before any `Person` node is written.

**Phase 1 - collect observations.** Every person-bearing field in section 3 of the task emits one observation with `email`, `source_id`, `name`, `origin` and `source_instance`. The queries are read-only and select only the columns needed for resolution. Normalisation: email stripped and lowercased, name stripped with internal whitespace collapsed and lowercased for comparison while the original casing is kept for display, `source_id` stripped but not lowercased, empty strings treated as missing.

**Phase 2 - cluster observations.** Union-find over the observations, strongest rule first:

- Rule A, email: same normalised email is the same person.
- Rule B, source_id: same `source_id` is the same person. `SOURCE_ID_IS_GLOBAL = True` at module level treats a source ID as globally unique across source instances, as `SQL_DATA_HANDOFF.md` instructs generators to reuse source IDs consistently. Setting it to `False` scopes rule B per `source_instance`.
- Rule C, transitive bridging: rules A and B share one union-find structure, so a Slack row or Teams participant entry carrying both an email and a source ID merges the two clusters. It is not a separate step.
- Rule D, name, conditional: a name-only observation attaches to an existing cluster only when exactly one cluster carries that normalised name. Zero matches create a new cluster. Two or more matches produce a separate cluster marked `identity_ambiguous`. A name never merges two established identities.

**Phase 3 - canonical key.** Precedence: `email:` + lowest-sorted email, else `source:` + lowest-sorted source ID, else `name:` + normalised name. Sorting keeps the key deterministic across imports.

**Phase 4 - `Person` nodes.** One node per cluster, written centrally from the clusters with `person_key`, `name`, `email`, `source_id`, `emails`, `source_ids`, `names`, `identity_confidence` and `identity_ambiguous`. The unique constraint on `person_key` is unchanged. The display name is the longest original-casing name in the cluster; ties prefer a name that carries real casing and then fall back to alphabetical order, so repeated imports pick the same name.

**Phase 5 - resolution at relationship time.** One shared function:

```python
resolve_person_key(email=None, source_id=None, name=None) -> str | None
```

### Changes in `viewer/app.py`

- Removed `person_key`, `issue_person_key` and `pr_person_key`. No `person_key` is built by string concatenation anywhere in the import path.
- Removed the `OPTIONAL MATCH (existing:Person {source_id: ...})` blocks in the Teams speaker, issue, document and pull request paths. Those were a runtime workaround for the keying problem and are now unnecessary.
- All six import paths resolve their person through `resolve_person_key`.
- Relationship creation now does `MATCH (p:Person {person_key: $person_key})` instead of `MERGE ... SET`, so per-row values can no longer overwrite the resolved cluster properties.
- Each import builds the registry over **all** source tables, not only the tables of the source group being imported, and writes the `Person` nodes before creating relationships. All six imports therefore agree on the same `Person` nodes regardless of which one runs.

### Idempotency: incremental merging

Stated in the module docstring and in `sync_person_nodes`. Canonical `Person` nodes are written first. Any pre-existing `Person` node whose key is no longer canonical is resolved through the same function, has its relationships moved to the canonical node, and is then deleted. Nothing is left orphaned. `MAIL_RECIPIENT` is handled separately because it points at the `Person` node rather than away from it, and its `recipient_type` property is preserved.

A full rebuild is not required, so relationships already imported from other source groups survive. Each import result message reports the merges as `old_key -> new_key`.

---

## 2. Verification

All checks were run against the local PostgreSQL and Neo4j instances.

### 2.1 Count and merge list

**Person nodes before: 4. After: 4. After a second full import run: 4.**

Merge list at key level, the keys the old rules produced against the new canonical keys, across all data in the database:

```
source:user-anna -> email:anna@example.com
```

That is the complete list: one merge. Distinct keys go from 5 to 4.

Per source path:

| Source field | Old key | New key |
| --- | --- | --- |
| `mail_messages.sender` | `email:customer@example.com` | unchanged |
| `mail_messages.recipient` | `email:product@example.com` | unchanged |
| `slack_messages.author` | `email:anna@example.com` | unchanged |
| `teams_meetings.participant` | `email:anna@example.com` | unchanged |
| `teams_transcript_segments.speaker` | `source:user-anna` | **`email:anna@example.com`** |
| `issue_versions.creator` | `source:user-anna` | **`email:anna@example.com`** |
| `issue_versions.assignee` | `source:user-erik` | unchanged |
| `issue_versions.changed_by` | `source:user-anna` | **`email:anna@example.com`** |
| `issue_comments.author` | `source:user-erik` | unchanged |
| `document_versions.author` | `source:user-anna` | **`email:anna@example.com`** |
| `pr_versions.author` | `source:user-erik` | unchanged |
| `pr_reviews.author` | `source:user-anna` | **`email:anna@example.com`** |

Collapsed keys: `['email:anna@example.com', 'source:user-anna']` -> `email:anna@example.com`.

**Why the node count is still 4 before and after.** The old code had a runtime shortcut beyond its key rules: `OPTIONAL MATCH (existing:Person {source_id: $source_id})` in the Teams speaker, issue, document and pull request paths reused an existing node with the same `source_id` when one already existed. The outcome therefore depended on import order. Because the Slack import ran before the issue, document and PR imports, the email-keyed node already carried `source_id = user-anna` and the later paths picked it up instead of creating `source:user-anna`. In the opposite order the graph would have held two Anna nodes. The merge above was already happening, but as an order-dependent side effect rather than a rule. It is now deterministic and resolved before the first node is written. This is read from the old code, not measured; the graph was not wiped to run the imports in reverse order.

The node migration list reported by `sync_person_nodes` during the import was therefore empty, because no superseded node was left in the graph:

```
Sammanslagna personnoder: (none)
```

The migration path itself was verified with an injected legacy node (see 2.6).

### 2.2 Single-person traversal

The person with the widest reach is `email:anna@example.com`:

```
{'label': ['Document'], 'n': 1}
{'label': ['PullRequest'], 'n': 1}
{'label': ['SlackMessage'], 'n': 1}
{'label': ['TeamsMeeting'], 'n': 1}
{'label': ['TeamsTranscriptSegment'], 'n': 1}
```

Five source labels, not six. The current test data holds one row per table and does not contain a person who appears in all six sources: Anna is absent from the single mail row, and the single issue is owned by Erik, because `OWNS_ISSUE` follows the assignee. Anna is the issue *creator*, but the import only relates the owner, which is existing behaviour and out of scope for this task.

Person-to-source coverage after the change:

| Person | Source labels reached |
| --- | --- |
| `email:anna@example.com` | Document, PullRequest, SlackMessage, TeamsMeeting, TeamsTranscriptSegment |
| `source:user-erik` | Issue, PullRequest |
| `email:customer@example.com` | MailMessage |
| `email:product@example.com` | MailMessage |

### 2.3 No orphan duplicates

The query from section 6.3 returns **zero rows**. No two `Person` nodes share an email or a source ID.

### 2.4 Over-merge check

No cluster has `size(emails) > 1`. There is nothing to review manually.

Identity flags across the graph: 4 nodes with `identity_confidence = "strong"`, 0 weak, 0 ambiguous.

### 2.5 Frontend unaffected

All seven graph endpoints return 200 and render:

| Filter | Status | Nodes | Relationships | Person nodes | Labels falling back to raw key |
| --- | --- | --- | --- | --- | --- |
| `All` | 200 | 11 | 11 | 4 | 0 |
| `Mail` | 200 | 3 | 2 | 2 | 0 |
| `Slack` | 200 | 2 | 1 | 1 | 0 |
| `Teams` | 200 | 3 | 3 | 1 | 0 |
| `Issues` | 200 | 2 | 2 | 1 | 0 |
| `Docs` | 200 | 2 | 1 | 1 | 0 |
| `PRs` | 200 | 3 | 2 | 2 | 0 |

Zero `Person` nodes are missing `name`, so the label selection in `backend/app.py` never falls back to `person_key`.

### 2.6 Additional checks

The test data is too small to exercise the rules, so they were verified separately.

**Rule-level checks.**

| Case | Expected | Result |
| --- | --- | --- |
| The six-source Anna case from the task, with mixed casing and spacing | 1 cluster | 1 cluster, key `email:anna@example.com`, `names` holds every spelling seen, the name-only transcript speaker resolves into it |
| Two different people both named "Anna", each with their own email and source ID | 2 clusters | 2 clusters, never merged |
| A name-only observation matching both of those | 3 clusters | 3 clusters; the orphan gets its own cluster with `identity_confidence = "weak"` and `identity_ambiguous = true` |
| A name matching exactly one cluster | attaches | attaches to `email:erik@example.com` |
| Key precedence with two emails in one cluster | lowest sorted email | `email:aa@example.com`, display name is the longest one |

**Live merge test.** A legacy name-keyed node was injected into the graph, holding a transcript relationship, and the Teams import was run:

```
Sammanslagna personnoder: 1 (name:anna -> email:anna@example.com)
```

The `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` relationship was moved to the canonical node, `name:anna` was deleted, and node and relationship counts returned exactly to the baseline.

**Idempotency.** Two consecutive full import runs over all six sources produced identical node counts and identical relationship counts.

---

## 3. Documentation Updated

**`docs/GRAPH_DATA_HANDOFF.md`.** The "Person key rules" list and the "Important" note stating that person identity is pragmatic rather than globally canonical are gone. The **Shared `Person` Model** section now documents the field inventory, the bridge rows that make the cross-source join possible, the four clustering rules, the `SOURCE_ID_IS_GLOBAL` flag, the key precedence, the full property table including `emails`, `source_ids`, `names`, `identity_confidence` and `identity_ambiguous`, the single resolver function, and the incremental merging approach to idempotency. The import flow section states that resolution runs over all source tables. The data generation guidance now asks for bridge rows and warns about duplicate names.

**`docs/SQL_DATA_HANDOFF.md`.** New section "Person Identity Across Sources" with the person field inventory, the observation that only three of the ten field groups carry an email, which rows act as bridges, and the generation rules that follow: give every person at least one bridge row, reuse source IDs across tables and source instances, reuse email spelling, avoid duplicate names for people who have neither an email nor a source ID, and fill in `speaker_source_id` where the source system would know it.

---

## Files Changed

| File | Change |
| --- | --- |
| `viewer/person_identity.py` | New. Observation collection, union-find clustering, canonical keys, registry and resolver. |
| `viewer/app.py` | All six import paths use the resolver. Inline key construction and the `source_id` reuse workaround removed. Canonical `Person` nodes and legacy node migration added. |
| `docs/GRAPH_DATA_HANDOFF.md` | Shared `Person` Model rewritten. |
| `docs/SQL_DATA_HANDOFF.md` | New person identity section. |
