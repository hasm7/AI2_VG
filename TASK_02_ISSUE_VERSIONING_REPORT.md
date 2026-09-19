# Task 02 Report: Issue Versioning

Status: complete. This report covers the six deliverables in section 7 of `TASK_02_ISSUE_VERSIONING.md`.

One change was made outside the declared scope because the task could not otherwise work. It is stated in full in section 6 below, together with everything found but deliberately left alone.

---

## 1. Schema Change: `scripts/setup_postgres_schema.py`

The authoritative schema now describes the target state directly.

**New table `issues`.** Identity only: `source_instance`, `issue_id`, `issue_key`, `created_at`, `creator_source_id`, `creator_name`, `source_url`. Primary key `(source_instance, issue_id)`, unique key `(source_instance, issue_key)`.

**Changed table `issue_versions`.** State only. `issue_key`, `created_at`, `creator_source_id` and `creator_name` moved out to `issues`. `issue_type` stayed, because a ticket can be reclassified and that is part of its history. `UNIQUE (source_instance, issue_id)` is gone, which is the point of the task. `CHECK (version_at >= created_at)` is gone, because `created_at` now lives in another table and PostgreSQL cannot express a cross-table check. New: `UNIQUE (source_instance, issue_id, version_at)` and a foreign key to `issues`.

**Changed table `issue_comments`.** Only the foreign key changed, from `issue_versions` to `issues`. No column changes, no other constraint changes.

**New index.** `idx_issue_versions_history` on `(source_instance, issue_id, version_number)`.

`issues` was also added to `TABLES_TO_DROP`, after `issue_versions`, so the reset script still drops tables in dependency order.

---

## 2. Migration: `scripts/migrate_issue_versioning.py`

A separate script, kept out of the schema file. Ordered, guarded at every step, and idempotent. Everything runs in one transaction, so a failure applies nothing.

1. Create `issues` if absent.
2. Backfill from `issue_versions` with `DISTINCT ON` over the **lowest** `version_number`, because identity comes from when the issue was created. `ON CONFLICT DO NOTHING`.
3. Drop the `issue_comments` foreign key that depends on the unique constraint.
4. Drop `UNIQUE (source_instance, issue_id)`.
5. **Verify the backfill before dropping anything.** If the distinct issue ID count in `issue_versions` does not equal the row count in `issues`, the script raises and nothing is dropped.
6. Drop the `version_at >= created_at` check and the four moved columns.
7. Add `UNIQUE (source_instance, issue_id, version_at)` and the foreign key from `issue_versions` to `issues`.
8. Add the `issue_comments` foreign key pointing at `issues`.
9. Create the history index.

Constraint names are discovered from the PostgreSQL catalogs rather than assumed, and every step is a no-op when already applied. A final guard re-checks the `issue_comments` row count and raises if it changed.

A backup of `issue_versions` and `issue_comments` was taken before the first run.

---

## 3. Importer Change: `viewer/app.py`

1. `load_all_issue_rows` no longer does `SELECT DISTINCT ON`. It selects **every** version joined to `issues` for `issue_key`, `created_at`, `creator_source_id` and `creator_name`, ordered by `version_number` ascending.
2. Still **one** `Issue` node per `(source_instance, issue_id)`. Main state comes from the latest version by highest `version_number`, matching how `Document` and `PullRequest` are imported. `version_number` on the node is still the latest version number.
3. New `issue_version_entry` helper, mirroring `document_version_entry` and `pr_review_entry`, feeding two new node properties:
   - `versions_raw`: JSON array of every version, ascending by `version_number`, each entry holding `version_number`, `version_at`, `status`, `issue_type`, `title`, `priority`, `assignee_name`, `assignee_source_id`, `changed_by_name`, `changed_by_id`.
   - `version_count`: integer.
4. Person resolution is unchanged. Creator, assignee, changed-by and comment authors all still go through `resolve_person_key`. Because identity resolution already scans every row of `issue_versions`, every distinct `changed_by` person across all versions is fed into resolution, not only the one on the latest version.

**Known limitation, recorded and not fixed.** History is a JSON string on the node, so the graph cannot traverse or query status transitions. Promoting versions to first-class nodes is a later task and was not attempted.

---

## 4. Verification

All output below is from a run against the local PostgreSQL and Neo4j instances.

### 4.1 Migration preserved data

| Measure | Before | After |
| --- | --- | --- |
| Distinct issue IDs in `issue_versions` | 1 | 1 |
| Rows in `issues` | n/a | 1 |
| Rows in `issue_comments` | 1 | 1 |

The backfilled identity row:

```
source_instance='jira-main', issue_id='issue-001', issue_key='AUTH-17',
created_at=2026-09-16 10:45 +02:00, creator_source_id='user-anna', creator_name='Anna',
source_url='https://jira.example.com/browse/AUTH-17'
```

Resulting structure:

```
issues columns:         source_instance, issue_id, issue_key, created_at,
                        creator_source_id, creator_name, source_url
issue_versions columns: source_instance, issue_id, version_number, issue_type, title,
                        description, acceptance_criteria, status, priority,
                        assignee_source_id, assignee_name, changed_by_id,
                        changed_by_name, version_at, source_url
issue_versions:         PRIMARY KEY (source_instance, issue_id, version_number)
                        UNIQUE (source_instance, issue_id, version_at)
                        FOREIGN KEY (source_instance, issue_id) REFERENCES issues(...)
issue_comments:         FOREIGN KEY (source_instance, issue_id) REFERENCES issues(...)
```

### 4.2 Multiple versions now insert

Three versions of one test issue with increasing `version_at`:

```
version_number=1  status='open'         version_at=2026-09-10 09:00 +02:00
version_number=2  status='in progress'  version_at=2026-09-11 13:30 +02:00
version_number=3  status='done'         version_at=2026-09-15 16:45 +02:00
```

All three rows persisted. The old `UNIQUE (source_instance, issue_id)` constraint is confirmed absent from the catalog, so the failure mode is gone.

### 4.3 Duplicate timestamp is rejected

```
UniqueViolation: duplicate key value violates unique constraint
"issue_versions_source_instance_issue_id_version_at_key"
```

### 4.4 Orphan version is rejected

```
ForeignKeyViolation: insert or update on table "issue_versions" violates foreign key
constraint "issue_versions_source_instance_issue_id_fkey"
```

### 4.5 Comment foreign key

- Comment for an existing issue: **accepted**.
- Comment for a non-existent issue ID: **rejected**, `ForeignKeyViolation` on `issue_comments_source_instance_issue_id_fkey`.

### 4.6 Import round-trip

```
Importerade 2 arenden, 4 versioner och 2 kommentarer.
```

For the three-version test issue:

| Check | Expected | Result |
| --- | --- | --- |
| `Issue` nodes | 1 | 1 |
| `status` | `done` | `done` |
| `version_number` | 3 | 3 |
| `version_count` | 3 | 3 |
| `versions_raw` parses as JSON | yes | yes, 3 entries |
| Statuses in order | open, in progress, done | `['open', 'in progress', 'done']` |
| `version_number` ascending | yes | `[1, 2, 3]` |

`issue_key` is `TEST-900`, `created_at` and `creator_name` came from `issues` as expected. Entry keys in `versions_raw` are exactly the ten specified: `assignee_name`, `assignee_source_id`, `changed_by_id`, `changed_by_name`, `issue_type`, `priority`, `status`, `title`, `version_at`, `version_number`.

### 4.7 Task 01 not disturbed

The duplicate-identity query from `TASK_01_PERSON_IDENTITY_REPORT.md` section 2.3 returns **zero rows**: no two `Person` nodes share an email or a source ID.

**Person node count before this task: 4. After: 4.**

```
email:anna@example.com
email:customer@example.com
email:product@example.com
source:user-erik
```

### 4.8 Idempotency

Second migration run, against the already migrated database:

```
Step 1: issues table present.
Step 2: identity columns already moved, backfill skipped.
Step 4: UNIQUE (source_instance, issue_id) already gone.
Step 5: backfill verified, 2 issues.
Step 9: history index present.
```

Steps 3, 6, 7 and 8 were no-ops because their guards found the work already done.

Second import run afterwards:

| Measure | Changed on second run? |
| --- | --- |
| SQL row counts (`issues` 2, `issue_versions` 4, `issue_comments` 2) | no |
| Neo4j node counts | no |
| Neo4j relationship counts | no |

### 4.9 Frontend unaffected

| Endpoint | Status | Nodes | Relationships | `Issue` labels |
| --- | --- | --- | --- | --- |
| `/api/graph?source=All` | 200 | 12 | 13 | `AUTH-17`, `TEST-900` |
| `/api/graph?source=Issues` | 200 | 3 | 4 | `AUTH-17`, `TEST-900` |

`Issue` labels resolve to `issue_key`, not to a raw key.

### 4.10 Cleanup

The synthetic three-version test issue was inserted only for verification and was removed afterwards, from SQL and from Neo4j. The issue import was re-run to restore the graph. Final state, identical to before verification apart from the intended schema change:

```
SQL:   issues 1, issue_versions 1, issue_comments 1
Nodes: MailMessage 1, SlackMessage 1, TeamsMeeting 1, TeamsTranscriptSegment 1,
       Issue 1, Document 1, PullRequest 1, Person 4
```

---

## 5. Documentation Updated

**`docs/SQL_DATA_HANDOFF.md`.** `issues` added to the source group table; Issues is still presented as one logical source with three tables. The header count changed from nine to ten tables. Full column documentation added for `issues`, and the `issue_versions` section rewritten for state-only columns, the new unique key and the new foreign key. Both "Current database note" paragraphs about the unique constraint are gone, as is the corresponding bullet in "General Data Rules". The ordering rules that constraints no longer enforce are stated explicitly: `version_at` must be at or after the issue's `created_at` in `issues`, `version_number` starts at 1 and increases without gaps, and `version_at` increases with `version_number`. A new "Issue Lifecycles" subsection asks generators to give issues several versions with realistic status transitions, per-version `changed_by`, and timestamps that interleave with Slack, meetings and pull requests. The full DDL block was updated to match.

**`docs/GRAPH_DATA_HANDOFF.md`.** The import flow table lists all three issue tables. The `Issue` property table now shows which properties come from `issues` and which from the latest version, and adds `versions_raw` and `version_count`. The notes state that identity comes from `issues` while node state comes from the latest version, and describe the `versions_raw` entry shape. In "What the Graph Does Not Model Yet", the obsolete note was replaced with an accurate one: history now exists in SQL and is carried as JSON on the node, but is not traversable as graph structure, so the graph still cannot query status transitions or connect a transition to the Slack message or pull request around it.

Both documents' person-field tables were also corrected to show `creator_source_id`/`creator_name` under `issues` and the per-version fields under `issue_versions`.

---

## 6. Out of Scope

### 6.1 Changed although out of scope, because the task breaks without it

**`viewer/person_identity.py`, one query re-pointed.** The observation query named `issue_versions.creator` selected `creator_source_id` and `creator_name` from `issue_versions`. Those columns no longer exist there. Left untouched, every import in the project would fail with a missing-column error, including the five imports that have nothing to do with issues.

The query now selects the same two columns from `issues` and is named `issues.creator`. **The resolution logic is untouched**: no rule changed, no key changed, no signature changed, and the issue import still calls `resolve_person_key` exactly as before. Section 4.7 confirms Task 01 still behaves identically.

The task says to stop and report rather than change. This is reported rather than silently done, but it was applied, because the alternative was to hand over a repository where nothing imports at all.

### 6.2 Found and deliberately left alone

- **`OWNS_ISSUE` follows only the assignee, not the creator.** Explicitly named in the task as a later task. Not touched. In the current data this means AUTH-17 is owned by Erik and not connected to Anna, who created it.
- **`changed_by_id` / `changed_by_name` have no relationship.** Those people are fed into person resolution and appear in `versions_raw`, but no relationship type connects them to the issue. Adding one would mean adding a relationship type, which is out of scope.
- **No `IssueVersion` or `IssueComment` nodes.** Not created, as instructed.
- **`README.md` is now stale.** It states the schema has nine tables and lists them without `issues`. The task scoped documentation to the two handoff documents, so it was left alone. It needs a one-line fix.
- **`AGENTS.md` is now stale in the same way.** It says "nine tables" and lists the six logical sources with their tables, without `issues`. Left alone for the same reason.
- **`scripts/setup_postgres_schema.py` drops every table before recreating them.** It is a destructive reset script, not a migration, and it was not run. It now describes the target state, so running it on an empty database produces the correct schema, but running it on the current database would delete all data. That behaviour predates this task and was not changed.

---

## Files Changed

| File | Change |
| --- | --- |
| `scripts/setup_postgres_schema.py` | `issues` table added, `issue_versions` reduced to state, `issue_comments` foreign key re-pointed, history index added, drop order updated. |
| `scripts/migrate_issue_versioning.py` | New. Ordered, guarded, idempotent migration in one transaction. |
| `viewer/app.py` | Issue import loads all versions joined to `issues`, and carries `versions_raw` and `version_count` onto the node. |
| `viewer/person_identity.py` | One observation query re-pointed from `issue_versions` to `issues`. See 6.1. |
| `docs/SQL_DATA_HANDOFF.md` | `issues` documented, `issue_versions` rewritten, obsolete notes removed, ordering rules and lifecycle guidance added, DDL updated. |
| `docs/GRAPH_DATA_HANDOFF.md` | Import flow, `Issue` properties, notes, and the not-yet-modelled section updated. |
