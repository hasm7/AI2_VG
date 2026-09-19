"""Migration: split issue identity out of issue_versions into issues.

Before this migration `issue_versions` held both issue identity and issue state,
and a UNIQUE (source_instance, issue_id) constraint allowed only one row per
issue. That constraint could not simply be dropped, because the foreign key on
`issue_comments` referenced it.

This script moves identity into a new `issues` table, re-points both foreign
keys at it, and frees `issue_versions` to hold one row per version.

The steps are ordered and each one is guarded, so the script is idempotent and
can be run again safely. Everything runs in one transaction: if any step fails,
nothing is applied.

Run with:

    .\\.venv\\Scripts\\python.exe scripts\\migrate_issue_versioning.py
"""

import os

import psycopg
from dotenv import load_dotenv


load_dotenv()


MOVED_COLUMNS = ["issue_key", "created_at", "creator_source_id", "creator_name"]


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "hm_data")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table_name}",))
    return cur.fetchone()[0]


def column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
        )
    """, (table_name, column_name))
    return cur.fetchone()[0]


def foreign_key_names(cur, table_name: str, referenced_table: str) -> list:
    cur.execute("""
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = %s::regclass
          AND contype = 'f'
          AND confrelid = %s::regclass
    """, (table_name, referenced_table))
    return [row[0] for row in cur.fetchall()]


def unique_constraint_name(cur, table_name: str, columns: list):
    cur.execute("""
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = %s::regclass
          AND c.contype = 'u'
          AND (
            SELECT array_agg(a.attname::text ORDER BY a.attname::text)
            FROM pg_attribute a
            WHERE a.attrelid = c.conrelid
              AND a.attnum = ANY (c.conkey)
          ) = %s
    """, (table_name, sorted(columns)))
    row = cur.fetchone()
    return row[0] if row else None


def check_constraint_names(cur, table_name: str, contains: str) -> list:
    cur.execute("""
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = %s::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE %s
    """, (table_name, f"%{contains}%"))
    return [row[0] for row in cur.fetchall()]


def distinct_issue_ids(cur, table_name: str) -> int:
    cur.execute(f"SELECT count(DISTINCT (source_instance, issue_id)) FROM {table_name}")
    return cur.fetchone()[0]


def migrate(cur) -> None:
    if not table_exists(cur, "issue_versions"):
        raise RuntimeError("issue_versions does not exist. Run scripts/setup_postgres_schema.py first.")

    versions_before = distinct_issue_ids(cur, "issue_versions")
    cur.execute("SELECT count(*) FROM issue_comments")
    comments_before = cur.fetchone()[0]
    print(f"Before: {versions_before} distinct issue IDs in issue_versions, {comments_before} issue_comments rows.")

    # Step 1: create the identity table.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            source_instance     TEXT NOT NULL,
            issue_id            TEXT NOT NULL,
            issue_key           TEXT NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL,
            creator_source_id   TEXT,
            creator_name        TEXT,
            source_url          TEXT,
            PRIMARY KEY (source_instance, issue_id),
            UNIQUE (source_instance, issue_key)
        )
    """)
    print("Step 1: issues table present.")

    # Step 2: backfill identity from the lowest version number of each issue,
    # because identity comes from when the issue was created.
    if column_exists(cur, "issue_versions", "issue_key"):
        cur.execute("""
            INSERT INTO issues (source_instance, issue_id, issue_key, created_at,
                                creator_source_id, creator_name, source_url)
            SELECT DISTINCT ON (source_instance, issue_id)
                   source_instance, issue_id, issue_key, created_at,
                   creator_source_id, creator_name, source_url
            FROM issue_versions
            ORDER BY source_instance, issue_id, version_number ASC
            ON CONFLICT DO NOTHING
        """)
        print(f"Step 2: backfilled {cur.rowcount} issue rows.")
    else:
        print("Step 2: identity columns already moved, backfill skipped.")

    # Step 3: drop the foreign key that depends on the unique constraint.
    for name in foreign_key_names(cur, "issue_comments", "issue_versions"):
        cur.execute(f'ALTER TABLE issue_comments DROP CONSTRAINT "{name}"')
        print(f"Step 3: dropped issue_comments foreign key {name}.")

    # Step 4: drop the unique constraint that blocked multiple versions.
    name = unique_constraint_name(cur, "issue_versions", ["source_instance", "issue_id"])
    if name:
        cur.execute(f'ALTER TABLE issue_versions DROP CONSTRAINT "{name}"')
        print(f"Step 4: dropped {name}.")
    else:
        print("Step 4: UNIQUE (source_instance, issue_id) already gone.")

    # Step 5: verify the backfill before anything is dropped.
    issues_count = distinct_issue_ids(cur, "issues")
    if issues_count != versions_before:
        raise RuntimeError(
            "Backfill incomplete: issue_versions has "
            f"{versions_before} distinct issue IDs but issues has {issues_count}. "
            "Nothing was dropped."
        )
    print(f"Step 5: backfill verified, {issues_count} issues.")

    # Step 6: drop the cross-table check and the moved columns.
    for name in check_constraint_names(cur, "issue_versions", "created_at"):
        cur.execute(f'ALTER TABLE issue_versions DROP CONSTRAINT "{name}"')
        print(f"Step 6: dropped check constraint {name}.")
    for column in MOVED_COLUMNS:
        if column_exists(cur, "issue_versions", column):
            cur.execute(f"ALTER TABLE issue_versions DROP COLUMN {column}")
            print(f"Step 6: dropped issue_versions.{column}.")

    # Step 7: the new version constraints.
    if not unique_constraint_name(cur, "issue_versions", ["source_instance", "issue_id", "version_at"]):
        cur.execute("""
            ALTER TABLE issue_versions
            ADD CONSTRAINT issue_versions_source_instance_issue_id_version_at_key
            UNIQUE (source_instance, issue_id, version_at)
        """)
        print("Step 7: added UNIQUE (source_instance, issue_id, version_at).")
    if not foreign_key_names(cur, "issue_versions", "issues"):
        cur.execute("""
            ALTER TABLE issue_versions
            ADD CONSTRAINT issue_versions_source_instance_issue_id_fkey
            FOREIGN KEY (source_instance, issue_id)
                REFERENCES issues (source_instance, issue_id)
        """)
        print("Step 7: added issue_versions foreign key to issues.")

    # Step 8: re-point the comment foreign key at the identity table.
    if not foreign_key_names(cur, "issue_comments", "issues"):
        cur.execute("""
            ALTER TABLE issue_comments
            ADD CONSTRAINT issue_comments_source_instance_issue_id_fkey
            FOREIGN KEY (source_instance, issue_id)
                REFERENCES issues (source_instance, issue_id)
        """)
        print("Step 8: added issue_comments foreign key to issues.")

    # Step 9: index for reading history in order.
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_issue_versions_history
            ON issue_versions (source_instance, issue_id, version_number)
    """)
    print("Step 9: history index present.")

    cur.execute("SELECT count(*) FROM issue_comments")
    comments_after = cur.fetchone()[0]
    versions_after = distinct_issue_ids(cur, "issue_versions")
    print(f"After: {versions_after} distinct issue IDs in issue_versions, "
          f"{issues_count} rows in issues, {comments_after} issue_comments rows.")

    if comments_after != comments_before:
        raise RuntimeError("issue_comments row count changed. Nothing was committed.")


def main() -> None:
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            migrate(cur)
    print("Issue versioning migration complete.")


if __name__ == "__main__":
    main()
