import os

import psycopg
from dotenv import load_dotenv


load_dotenv()


OLD_TABLES_TO_DROP = [
    # Put the old table name here before running, for example:
    # "old_table_name",
]


TABLES_TO_DROP = [
    "pr_reviews",
    "pr_versions",
    "document_versions",
    "issue_comments",
    "issue_versions",
    "issues",
    "teams_transcript_segments",
    "teams_meetings",
    "slack_messages",
    "mail_messages",
]


SCHEMA_SQL = """
CREATE TABLE mail_messages (
    source_instance     TEXT NOT NULL,
    message_id          TEXT NOT NULL,
    sender_address      TEXT NOT NULL,
    sender_name         TEXT,
    recipients          JSONB NOT NULL,
    subject             TEXT,
    body                TEXT NOT NULL,
    sent_at             TIMESTAMPTZ NOT NULL,
    in_reply_to_id      TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, message_id),
    CHECK (jsonb_typeof(recipients) = 'array')
);

CREATE TABLE slack_messages (
    source_instance     TEXT NOT NULL,
    message_id          TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    workspace_id        TEXT NOT NULL,
    channel_id          TEXT NOT NULL,
    channel_name        TEXT,
    author_source_id    TEXT,
    author_name         TEXT,
    author_email        TEXT,
    body                TEXT NOT NULL,
    sent_at             TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    thread_root_id      TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, workspace_id, channel_id, message_id, version_number),
    CHECK (version_at >= sent_at)
);

CREATE TABLE teams_meetings (
    source_instance     TEXT NOT NULL,
    meeting_id          TEXT NOT NULL,
    title               TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    participants        JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_url          TEXT,
    PRIMARY KEY (source_instance, meeting_id),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (jsonb_typeof(participants) = 'array')
);

CREATE TABLE teams_transcript_segments (
    source_instance     TEXT NOT NULL,
    meeting_id          TEXT NOT NULL,
    segment_id          TEXT NOT NULL,
    sequence_number     INTEGER NOT NULL CHECK (sequence_number > 0),
    speaker_source_id   TEXT,
    speaker_name        TEXT,
    start_offset_ms     BIGINT NOT NULL CHECK (start_offset_ms >= 0),
    end_offset_ms       BIGINT,
    body                TEXT NOT NULL,
    PRIMARY KEY (source_instance, meeting_id, segment_id),
    UNIQUE (source_instance, meeting_id, sequence_number),
    FOREIGN KEY (source_instance, meeting_id)
        REFERENCES teams_meetings (source_instance, meeting_id),
    CHECK (end_offset_ms IS NULL OR end_offset_ms >= start_offset_ms)
);

CREATE TABLE issues (
    source_instance     TEXT NOT NULL,
    issue_id            TEXT NOT NULL,
    issue_key           TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    creator_source_id   TEXT,
    creator_name        TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, issue_id),
    UNIQUE (source_instance, issue_key)
);

CREATE TABLE issue_versions (
    source_instance     TEXT NOT NULL,
    issue_id            TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    issue_type          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT,
    acceptance_criteria TEXT,
    status              TEXT NOT NULL,
    priority            TEXT,
    assignee_source_id  TEXT,
    assignee_name       TEXT,
    changed_by_id       TEXT,
    changed_by_name     TEXT,
    version_at          TIMESTAMPTZ NOT NULL,
    source_url          TEXT,
    PRIMARY KEY (source_instance, issue_id, version_number),
    UNIQUE (source_instance, issue_id, version_at),
    FOREIGN KEY (source_instance, issue_id)
        REFERENCES issues (source_instance, issue_id)
);

CREATE TABLE issue_comments (
    source_instance     TEXT NOT NULL,
    comment_id          TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    issue_id            TEXT NOT NULL,
    author_source_id    TEXT,
    author_name         TEXT,
    body                TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    reply_to_comment_id TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, comment_id, version_number),
    FOREIGN KEY (source_instance, issue_id)
        REFERENCES issues (source_instance, issue_id),
    CHECK (version_at >= created_at)
);

CREATE TABLE document_versions (
    source_instance     TEXT NOT NULL,
    document_id         TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    document_type       TEXT NOT NULL,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    content_format      TEXT NOT NULL DEFAULT 'markdown',
    author_source_id    TEXT,
    author_name         TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    change_summary      TEXT,
    source_url          TEXT,
    PRIMARY KEY (source_instance, document_id, version_number),
    CHECK (version_at >= created_at)
);

CREATE TABLE pr_versions (
    source_instance     TEXT NOT NULL,
    repository          TEXT NOT NULL,
    pr_number           INTEGER NOT NULL CHECK (pr_number > 0),
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    title               TEXT NOT NULL,
    description         TEXT,
    author_source_id    TEXT,
    author_name         TEXT,
    state               TEXT NOT NULL CHECK (state IN ('open', 'closed', 'merged')),
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    base_commit         TEXT NOT NULL,
    head_commit         TEXT NOT NULL,
    code_changes        JSONB NOT NULL,
    source_url          TEXT,
    PRIMARY KEY (source_instance, repository, pr_number, version_number),
    CHECK (version_at >= created_at),
    CHECK (jsonb_typeof(code_changes) = 'array')
);

CREATE TABLE pr_reviews (
    source_instance     TEXT NOT NULL,
    repository          TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    entry_type          TEXT NOT NULL CHECK (entry_type IN ('comment', 'approved', 'changes_requested', 'commented')),
    source_id           TEXT NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    pr_version_number   INTEGER NOT NULL,
    reviewed_commit     TEXT,
    author_source_id    TEXT,
    author_name         TEXT,
    body                TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    version_at          TIMESTAMPTZ NOT NULL,
    reply_to_source_id  TEXT,
    review_group_id     TEXT,
    file_path           TEXT,
    line_number         INTEGER CHECK (line_number > 0),
    diff_side           TEXT CHECK (diff_side IN ('before', 'after')),
    source_url          TEXT,
    PRIMARY KEY (source_instance, repository, pr_number, source_id, version_number),
    FOREIGN KEY (source_instance, repository, pr_number, pr_version_number)
        REFERENCES pr_versions (source_instance, repository, pr_number, version_number),
    CHECK (version_at >= created_at),
    CHECK (
        (line_number IS NULL AND diff_side IS NULL)
        OR
        (line_number IS NOT NULL AND diff_side IS NOT NULL AND file_path IS NOT NULL AND reviewed_commit IS NOT NULL)
    )
);

CREATE INDEX idx_mail_reply
    ON mail_messages (source_instance, in_reply_to_id);

CREATE INDEX idx_slack_thread
    ON slack_messages (source_instance, workspace_id, channel_id, thread_root_id, sent_at);

CREATE INDEX idx_issue_versions_history
    ON issue_versions (source_instance, issue_id, version_number);

CREATE INDEX idx_issue_comments_issue
    ON issue_comments (source_instance, issue_id, created_at);

CREATE INDEX idx_pr_reviews_pr_version
    ON pr_reviews (source_instance, repository, pr_number, pr_version_number);
"""


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


def drop_table(cur, table_name: str) -> None:
    cur.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')


def main() -> None:
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            for table_name in OLD_TABLES_TO_DROP:
                drop_table(cur, table_name)

            for table_name in TABLES_TO_DROP:
                drop_table(cur, table_name)

            cur.execute(SCHEMA_SQL)

    print("Postgres schema created.")


if __name__ == "__main__":
    main()
