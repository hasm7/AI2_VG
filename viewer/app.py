import json
import os
from datetime import date, datetime, time
from decimal import Decimal

import psycopg
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template_string, request, url_for
from neo4j import GraphDatabase
from psycopg.rows import dict_row


load_dotenv()


app = Flask(__name__)

MAX_ROWS = 50

SOURCES = {
    "mail": "Mail",
    "slack": "Slack - projektchatt",
    "teams": "Teams - motestranskript",
    "issues": "Arenden/tickets",
    "documents": "Krav och teknisk dokumentation",
    "prs": "PR:er, kodgranskningar och kodandringar",
}

PAGE = """
<!doctype html>
<html lang="sv">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI2 Data Viewer</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Arial, sans-serif; color: #1f2937; background: #f8fafc; }
    .layout { display: grid; grid-template-columns: 220px 340px 1fr; min-height: 100vh; }
    aside { background: #111827; color: white; padding: 18px; }
    aside h1 { font-size: 18px; margin: 0 0 18px; }
    aside a { display: block; color: #d1d5db; text-decoration: none; padding: 9px 10px; border-radius: 6px; margin-bottom: 4px; }
    aside a.active, aside a:hover { background: #374151; color: white; }
    .list { border-right: 1px solid #e5e7eb; background: white; padding: 18px; overflow: auto; }
    .list h2, .detail h2 { margin: 0 0 8px; font-size: 18px; }
    .limit { margin: 0 0 14px; color: #6b7280; font-size: 13px; }
    .item { display: block; text-decoration: none; color: inherit; border: 1px solid #e5e7eb; border-radius: 6px; padding: 10px; margin-bottom: 8px; background: #fff; }
    .item.active { border-color: #2563eb; background: #eff6ff; }
    .item strong { display: block; font-size: 14px; margin-bottom: 4px; }
    .item span { color: #6b7280; font-size: 13px; }
    .detail { padding: 18px 24px; overflow: auto; }
    .tabs { margin: 0 0 14px; }
    .tabs a { display: inline-block; text-decoration: none; color: #374151; border: 1px solid #d1d5db; padding: 7px 10px; border-radius: 6px; margin-right: 6px; background: white; font-size: 14px; }
    .tabs a.active { background: #2563eb; color: white; border-color: #2563eb; }
    .import-box { background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 6px; padding: 12px 14px; margin: 0 0 14px; }
    .import-box form { margin: 0; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .import-box button { border: 0; border-radius: 6px; background: #047857; color: white; padding: 8px 10px; cursor: pointer; }
    .import-box input { border: 1px solid #a7f3d0; border-radius: 6px; padding: 7px 8px; min-width: 170px; }
    .import-box p { margin: 8px 0 0; font-size: 13px; color: #065f46; }
    .error { background: #fef2f2; border-color: #fecaca; }
    .error p { color: #991b1b; }
    .panel { background: white; border: 1px solid #e5e7eb; border-radius: 6px; margin: 14px 0; }
    .panel h3 { margin: 0; padding: 12px 14px; border-bottom: 1px solid #e5e7eb; font-size: 15px; }
    .field { border-bottom: 1px solid #e5e7eb; padding: 12px 14px; }
    .field:last-child { border-bottom: 0; }
    .field label { display: block; font-size: 12px; font-weight: bold; color: #6b7280; text-transform: uppercase; margin-bottom: 6px; }
    pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: Consolas, monospace; font-size: 13px; }
    .empty { color: #6b7280; }
    .table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 6px; background: white; }
    table { border-collapse: collapse; width: max-content; min-width: 100%; font-size: 13px; }
    th, td { border-bottom: 1px solid #e5e7eb; border-right: 1px solid #e5e7eb; padding: 8px 10px; vertical-align: top; max-width: 360px; }
    th { position: sticky; top: 0; background: #f3f4f6; text-align: left; font-size: 12px; color: #374151; }
    td { background: white; }
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <h1>AI2 Data Viewer</h1>
      {% for key, label in sources.items() %}
        <a class="{{ 'active' if key == active_source else '' }}" href="/?source={{ key }}&view={{ view_mode }}">{{ label }}</a>
      {% endfor %}
    </aside>

    <main class="list">
      <h2>{{ sources[active_source] }}</h2>
      <p class="limit">Visar max {{ max_rows }} rader.</p>
      {% for row in rows %}
        <a class="item {{ 'active' if loop.index0 == active_index else '' }}" href="/?source={{ active_source }}&row={{ loop.index0 }}&view={{ view_mode }}">
          <strong>{{ row.title }}</strong>
          <span>{{ row.subtitle }}</span>
        </a>
      {% else %}
        <p class="empty">Inga rader.</p>
      {% endfor %}
    </main>

    <section class="detail">
      <h2>{{ 'Tabellvy' if view_mode == 'table' else 'Detaljer' }}</h2>
      <div class="tabs">
        <a class="{{ 'active' if view_mode == 'detail' else '' }}" href="/?source={{ active_source }}&row={{ active_index }}&view=detail">JSON/detaljvy</a>
        <a class="{{ 'active' if view_mode == 'table' else '' }}" href="/?source={{ active_source }}&row={{ active_index }}&view=table">Tabellvy</a>
      </div>

      {% if active_source == 'mail' %}
        <div class="import-box {{ 'error' if import_error else '' }}">
          <form method="post" action="/import/mail">
            <input name="neo4j_uri" value="{{ neo4j_uri }}" aria-label="Neo4j URI">
            <input name="neo4j_database" value="{{ neo4j_database }}" aria-label="Neo4j database">
            <input name="neo4j_user" value="{{ neo4j_user }}" aria-label="Neo4j user">
            <input name="neo4j_password" type="password" placeholder="Neo4j password" aria-label="Neo4j password">
            <button type="submit">Importera Mail</button>
          </form>
          {% if import_result %}<p>{{ import_result }}</p>{% endif %}
          {% if import_error %}<p>{{ import_error }}</p>{% endif %}
        </div>
      {% endif %}

      {% if view_mode == 'table' %}
        {% if table_rows %}
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  {% for column in table_columns %}<th>{{ column }}</th>{% endfor %}
                </tr>
              </thead>
              <tbody>
                {% for table_row in table_rows %}
                  <tr>
                    {% for column in table_columns %}<td><pre>{{ table_row[column] }}</pre></td>{% endfor %}
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        {% else %}
          <p class="empty">Inga rader.</p>
        {% endif %}
      {% elif panels %}
        {% for panel in panels %}
          <div class="panel">
            <h3>{{ panel.title }}</h3>
            {% for field, value in panel.fields.items() %}
              <div class="field">
                <label>{{ field }}</label>
                <pre>{{ value }}</pre>
              </div>
            {% endfor %}
          </div>
        {% endfor %}
      {% else %}
        <p class="empty">Ingen rad vald.</p>
      {% endif %}
    </section>
  </div>
</body>
</html>
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


def neo4j_defaults() -> dict:
    return {
        "uri": os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687"),
        "database": os.getenv("NEO4J_DATABASE", "neo4j"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
    }


def format_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date, time, Decimal)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def pick(row, fields):
    return {field: format_value(row.get(field)) for field in fields if field in row}


def query_all(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def person_key(name, email):
    if email:
        return f"email:{email.strip().lower()}"
    if name:
        return f"name:{name.strip().lower()}"
    return None


def load_all_mail_rows():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT *
                FROM mail_messages
                ORDER BY sent_at, source_instance, message_id
            """)


def ensure_mail_graph_schema(tx):
    tx.run("""
        CREATE CONSTRAINT mail_message_key IF NOT EXISTS
        FOR (m:MailMessage)
        REQUIRE (m.source_instance, m.message_id) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT person_key IF NOT EXISTS
        FOR (p:Person)
        REQUIRE p.person_key IS UNIQUE
    """)


def import_mail_row(tx, row):
    recipients = row.get("recipients") or []
    recipients_raw = json.dumps(recipients, ensure_ascii=False)

    tx.run("""
        MERGE (m:MailMessage {
            source_instance: $source_instance,
            message_id: $message_id
        })
        SET m.sender_address = $sender_address,
            m.sender_name = $sender_name,
            m.recipients_raw = $recipients_raw,
            m.subject = $subject,
            m.body = $body,
            m.sent_at = $sent_at,
            m.in_reply_to_id = $in_reply_to_id,
            m.source_url = $source_url
    """, {
        "source_instance": row["source_instance"],
        "message_id": row["message_id"],
        "sender_address": row["sender_address"],
        "sender_name": row["sender_name"],
        "recipients_raw": recipients_raw,
        "subject": row["subject"],
        "body": row["body"],
        "sent_at": row["sent_at"].isoformat(),
        "in_reply_to_id": row["in_reply_to_id"],
        "source_url": row["source_url"],
    })

    sender_key = person_key(row["sender_name"], row["sender_address"])
    if sender_key:
        tx.run("""
            MATCH (m:MailMessage {
                source_instance: $source_instance,
                message_id: $message_id
            })
            MERGE (p:Person {person_key: $person_key})
            SET p.name = coalesce($name, p.name),
                p.email = coalesce($email, p.email)
            MERGE (p)-[:SENT]->(m)
        """, {
            "source_instance": row["source_instance"],
            "message_id": row["message_id"],
            "person_key": sender_key,
            "name": row["sender_name"],
            "email": row["sender_address"],
        })

    for recipient in recipients:
        name = recipient.get("name")
        email = recipient.get("address")
        key = person_key(name, email)
        if not key:
            continue
        tx.run("""
            MATCH (m:MailMessage {
                source_instance: $source_instance,
                message_id: $message_id
            })
            MERGE (p:Person {person_key: $person_key})
            SET p.name = coalesce($name, p.name),
                p.email = coalesce($email, p.email)
            MERGE (m)-[r:SENT_TO]->(p)
            SET r.recipient_type = $recipient_type
        """, {
            "source_instance": row["source_instance"],
            "message_id": row["message_id"],
            "person_key": key,
            "name": name,
            "email": email,
            "recipient_type": recipient.get("type"),
        })


def import_mail_to_neo4j(uri, user, password, database):
    rows = load_all_mail_rows()
    if not password:
        raise RuntimeError("Neo4j password is required.")

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.execute_write(ensure_mail_graph_schema)
            for row in rows:
                session.execute_write(import_mail_row, row)

            node_counts = session.execute_read(lambda tx: tx.run("""
                MATCH (n)
                WHERE n:MailMessage OR n:Person
                RETURN labels(n)[0] AS label, count(n) AS count
                ORDER BY label
            """).data())

    counts = ", ".join(f"{item['label']}: {item['count']}" for item in node_counts)
    return f"Importerade {len(rows)} mailrader. Grafen innehåller nu {counts}."


def load_mail(cur):
    return query_all(cur, """
        SELECT *, COALESCE(subject, message_id) AS title, sender_name AS subtitle
        FROM mail_messages
        ORDER BY sent_at DESC
        LIMIT %s
    """, (MAX_ROWS,))


def detail_mail(cur, row):
    return [{"title": "Mail", "fields": pick(row, ["subject", "sender_name", "sender_address", "recipients", "body", "sent_at", "in_reply_to_id", "source_url"])}]


def table_mail(cur):
    return load_mail(cur)


def load_slack(cur):
    return query_all(cur, """
        SELECT *, message_id AS title, CONCAT(channel_name, ' - ', author_name) AS subtitle
        FROM slack_messages
        ORDER BY sent_at DESC
        LIMIT %s
    """, (MAX_ROWS,))


def detail_slack(cur, row):
    return [{"title": "Slack message", "fields": pick(row, ["channel_name", "author_name", "author_email", "body", "sent_at", "thread_root_id", "source_url"])}]


def table_slack(cur):
    return load_slack(cur)


def load_teams(cur):
    return query_all(cur, """
        SELECT *, title, meeting_id AS subtitle
        FROM teams_meetings
        ORDER BY started_at DESC
        LIMIT %s
    """, (MAX_ROWS,))


def detail_teams(cur, row):
    segments = query_all(cur, """
        SELECT *
        FROM teams_transcript_segments
        WHERE source_instance = %s AND meeting_id = %s
        ORDER BY sequence_number
        LIMIT %s
    """, (row["source_instance"], row["meeting_id"], MAX_ROWS))
    transcript = "\n\n".join(f'{segment["sequence_number"]}. {segment.get("speaker_name") or "Unknown"}: {segment["body"]}' for segment in segments)
    return [
        {"title": "Teams meeting", "fields": pick(row, ["title", "started_at", "ended_at", "participants", "source_url"])},
        {"title": "Transcript segments", "fields": {"segments": transcript}},
    ]


def table_teams(cur):
    return query_all(cur, """
        SELECT
            m.source_instance,
            m.meeting_id,
            m.title,
            m.started_at,
            m.ended_at,
            m.participants,
            s.segment_id,
            s.sequence_number,
            s.speaker_name,
            s.body AS transcript_body
        FROM teams_meetings m
        LEFT JOIN teams_transcript_segments s
            ON s.source_instance = m.source_instance AND s.meeting_id = m.meeting_id
        ORDER BY m.started_at DESC, s.sequence_number
        LIMIT %s
    """, (MAX_ROWS,))


def load_issues(cur):
    return query_all(cur, """
        SELECT DISTINCT ON (source_instance, issue_id) *, issue_key AS title, title AS subtitle
        FROM issue_versions
        ORDER BY source_instance, issue_id, version_number DESC
        LIMIT %s
    """, (MAX_ROWS,))


def detail_issues(cur, row):
    comments = query_all(cur, """
        SELECT *
        FROM issue_comments
        WHERE source_instance = %s AND issue_id = %s
        ORDER BY created_at
        LIMIT %s
    """, (row["source_instance"], row["issue_id"], MAX_ROWS))
    comment_text = "\n\n".join(f'{comment.get("author_name") or "Unknown"}: {comment["body"]}' for comment in comments)
    return [
        {"title": "Issue latest version", "fields": pick(row, ["issue_key", "issue_type", "title", "description", "acceptance_criteria", "status", "priority", "assignee_name", "version_at", "source_url"])},
        {"title": "Issue comments", "fields": {"comments": comment_text}},
    ]


def table_issues(cur):
    return query_all(cur, """
        SELECT
            i.source_instance,
            i.issue_id,
            i.version_number,
            i.issue_key,
            i.issue_type,
            i.title,
            i.status,
            i.priority,
            i.assignee_name,
            i.acceptance_criteria,
            c.comment_id,
            c.author_name AS comment_author,
            c.body AS comment_body,
            c.created_at AS comment_created_at
        FROM issue_versions i
        LEFT JOIN issue_comments c
            ON c.source_instance = i.source_instance AND c.issue_id = i.issue_id
        ORDER BY i.version_at DESC, c.created_at
        LIMIT %s
    """, (MAX_ROWS,))


def load_documents(cur):
    return query_all(cur, """
        SELECT DISTINCT ON (source_instance, document_id) *, title, document_type AS subtitle
        FROM document_versions
        ORDER BY source_instance, document_id, version_number DESC
        LIMIT %s
    """, (MAX_ROWS,))


def detail_documents(cur, row):
    return [{"title": "Document latest version", "fields": pick(row, ["document_type", "title", "body", "author_name", "version_at", "change_summary", "source_url"])}]


def table_documents(cur):
    return load_documents(cur)


def load_prs(cur):
    return query_all(cur, """
        SELECT DISTINCT ON (source_instance, repository, pr_number) *, title, CONCAT(repository, ' #', pr_number) AS subtitle
        FROM pr_versions
        ORDER BY source_instance, repository, pr_number, version_number DESC
        LIMIT %s
    """, (MAX_ROWS,))


def detail_prs(cur, row):
    reviews = query_all(cur, """
        SELECT *
        FROM pr_reviews
        WHERE source_instance = %s AND repository = %s AND pr_number = %s
        ORDER BY created_at
        LIMIT %s
    """, (row["source_instance"], row["repository"], row["pr_number"], MAX_ROWS))
    review_text = "\n\n".join(f'{review["entry_type"]} - {review.get("author_name") or "Unknown"}: {review.get("body") or ""}' for review in reviews)
    return [
        {"title": "PR latest version", "fields": pick(row, ["repository", "pr_number", "title", "description", "state", "base_commit", "head_commit", "code_changes", "source_url"])},
        {"title": "Reviews and comments", "fields": {"reviews": review_text}},
    ]


def table_prs(cur):
    return query_all(cur, """
        SELECT
            p.source_instance,
            p.repository,
            p.pr_number,
            p.version_number,
            p.title,
            p.state,
            p.description,
            p.code_changes,
            r.entry_type,
            r.source_id AS review_source_id,
            r.author_name AS review_author,
            r.body AS review_body,
            r.file_path,
            r.line_number,
            r.diff_side
        FROM pr_versions p
        LEFT JOIN pr_reviews r
            ON r.source_instance = p.source_instance AND r.repository = p.repository AND r.pr_number = p.pr_number
        ORDER BY p.version_at DESC, r.created_at
        LIMIT %s
    """, (MAX_ROWS,))


LOADERS = {
    "mail": (load_mail, detail_mail, table_mail),
    "slack": (load_slack, detail_slack, table_slack),
    "teams": (load_teams, detail_teams, table_teams),
    "issues": (load_issues, detail_issues, table_issues),
    "documents": (load_documents, detail_documents, table_documents),
    "prs": (load_prs, detail_prs, table_prs),
}


def format_table(rows):
    if not rows:
        return [], []
    columns = list(rows[0].keys())
    table_rows = []
    for row in rows:
        table_rows.append({column: format_value(row.get(column)) for column in columns})
    return columns, table_rows


@app.get("/")
def index():
    active_source = request.args.get("source", "mail")
    if active_source not in SOURCES:
        abort(404)

    view_mode = request.args.get("view", "detail")
    if view_mode not in {"detail", "table"}:
        view_mode = "detail"

    load_rows, load_detail, load_table = LOADERS[active_source]
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            rows = load_rows(cur)
            active_index = int(request.args.get("row", 0)) if rows else -1
            active_index = max(0, min(active_index, len(rows) - 1)) if rows else -1
            panels = load_detail(cur, rows[active_index]) if rows else []
            table_columns, table_rows = format_table(load_table(cur)) if view_mode == "table" else ([], [])

    display_rows = []
    for row in rows:
        display_rows.append({"title": format_value(row.get("title")) or "(untitled)", "subtitle": format_value(row.get("subtitle"))})

    defaults = neo4j_defaults()
    return render_template_string(
        PAGE,
        sources=SOURCES,
        active_source=active_source,
        rows=display_rows,
        active_index=active_index,
        panels=panels,
        table_columns=table_columns,
        table_rows=table_rows,
        max_rows=MAX_ROWS,
        view_mode=view_mode,
        import_result=request.args.get("import_result", ""),
        import_error=request.args.get("import_error", ""),
        neo4j_uri=defaults["uri"],
        neo4j_database=defaults["database"],
        neo4j_user=defaults["user"],
    )


@app.post("/import/mail")
def import_mail():
    uri = request.form.get("neo4j_uri") or os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    database = request.form.get("neo4j_database") or os.getenv("NEO4J_DATABASE", "neo4j")
    user = request.form.get("neo4j_user") or os.getenv("NEO4J_USER", "neo4j")
    password = request.form.get("neo4j_password") or os.getenv("NEO4J_PASSWORD", "")

    try:
        result = import_mail_to_neo4j(uri, user, password, database)
        return redirect(url_for("index", source="mail", import_result=result))
    except Exception as error:
        return redirect(url_for("index", source="mail", import_error=str(error)))


if __name__ == "__main__":
    app.run(debug=True)
