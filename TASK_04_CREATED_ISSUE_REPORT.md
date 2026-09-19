# Task 04 Report: CREATED_ISSUE relationship

Status: complete.

---

## 1. What changed

### `viewer/app.py`

`CREATED_ISSUE` added to the `ISSUE_PERSON_RELATIONSHIPS` whitelist, and the relationship created in `import_issue_row` from the creator fields that the issue import already joins in from `issues`:

```python
creator_key = registry.resolve_person_key(
    source_id=row["creator_source_id"],
    name=row["creator_name"],
)
merge_issue_person(tx, row, "CREATED_ISSUE", creator_key)
```

It reuses the existing `merge_issue_person` helper, so it follows the established pattern exactly: the person is resolved through `resolve_person_key`, the `Person` node is reached with `MATCH` on `person_key`, and the relationship is created with `MERGE`. No key is built any other way, no placeholder node is created, and the relationship carries no properties. When the creator is missing or does not resolve, `merge_issue_person` returns without creating anything.

`CREATED_ISSUE` was also added to `PERSON_OUTGOING_RELATIONSHIPS`. See section 4.

### `backend/app.py`

`CREATED_ISSUE` added to the `Issues` filter group. Nothing else in that file was touched.

### `docs/GRAPH_DATA_HANDOFF.md`

Four places, as specified: the import flow table, the relationship block in the Issues section, the "Relationship Types by Frontend Filter" table, and a note stating that `CREATED_ISSUE` comes from `issues.creator_source_id` while `OWNS_ISSUE` follows the assignee of the latest version, and that the two are deliberately separate.

`docs/SQL_DATA_HANDOFF.md` was not changed. No SQL model is affected.

---

## 2. Result

**Relationships: 123 before, 125 after. Nodes unchanged at 68.**

The two relationships created:

```
Anna Berg     (email:anna.berg@example.com)     -> AUTH-17
Erik Nilsson  (email:erik.nilsson@example.com)  -> AUTH-19
```

Anna Berg now reaches AUTH-17 four ways, where the creator edge was previously missing:

```
CREATED_ISSUE          -> AUTH-17
COMMENTED_ON_ISSUE     -> AUTH-17
CHANGED_ISSUE_VERSION  -> AUTH-17 v1
CHANGED_ISSUE_VERSION  -> AUTH-17 v3
```

`OWNS_ISSUE` is unchanged and still follows the assignee:

```
Anna Lindqvist -> AUTH-17
Erik Nilsson   -> AUTH-19
```

Re-running the issue import left both counts at 68 and 125, so the import stays idempotent. The Task 01 duplicate-identity check returns zero rows and `Person` is still 7.

All seven backend filters answer 200. The `Issues` filter went from 36 to 38 relationships, which is the two new edges. No other filter changed.

---

## 3. Verification output

```
BEFORE: 68 nodes, 123 relationships
        CREATED_ISSUE already present: 0

Imported 2 issues, 7 versions and 5 comments.

AFTER:  68 nodes, 125 relationships     nodes 68: True   relationships 125: True   (+2)

CREATED_ISSUE
  Anna Berg       (email:anna.berg@example.com   ) -> AUTH-17
  Erik Nilsson    (email:erik.nilsson@example.com) -> AUTH-19

OWNS_ISSUE unchanged
  Anna Lindqvist  -> AUTH-17
  Erik Nilsson    -> AUTH-19

Idempotency: second run  68 nodes, 125 relationships  unchanged: True
Task 01 duplicate check: no duplicates.   Person: 7

Backend filters
  All      68 nodes, 125 relationships
  Mail      9 nodes,  12 relationships
  Slack    16 nodes,  14 relationships
  Teams    16 nodes,  26 relationships
  Issues   18 nodes,  38 relationships
  Docs      7 nodes,   9 relationships
  PRs      18 nodes,  26 relationships
```

---

## 4. Out of scope

**One change made beyond the literal list, and why.** `CREATED_ISSUE` was added to `PERSON_OUTGOING_RELATIONSHIPS` in `viewer/app.py`. That constant is not a relationship definition; it is the list `move_person_relationships` walks when a superseded `Person` node is merged into its canonical node. A relationship type missing from that list is left behind on the old node when a person key changes, which breaks the Task 01 guarantee that nothing is orphaned. Every person-outgoing relationship added in Task 03 was registered there for the same reason, so this follows the established pattern rather than introducing anything new. No existing entry was modified.

**Found and deliberately left unchanged:**

- `OWNS_ISSUE` keeps its assignee-with-fallback behaviour, untouched, as instructed.
- `viewer/person_identity.py` was not modified. The task calls `resolve_person_key`, it does not change it.
- No SQL schema change, no new label, no new constraint.
- `AGENTS.md` and `README.md` still state that the schema has nine tables. It has ten since Task 02. This has been reported since that task and remains outside the scope of each task since.
- `backend/app.py` runs with `debug=True`, so the Werkzeug reloader picked up the filter-group change without a restart. That is convenient in development but is a pre-existing setting and was not changed.
