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
    .import-graph { margin: 0 0 10px; font-family: Consolas, monospace; font-size: 13px; color: #064e3b; }
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
          <div class="import-graph">
            (:Person)-[:SENT_MAIL]-&gt;(:MailMessage)-[:MAIL_RECIPIENT {recipient_type}]-&gt;(:Person)
          </div>
          <form method="post" action="/import/mail">
            <button type="submit">Importera alla mail</button>
          </form>
          {% if import_result %}<p>{{ import_result }}</p>{% endif %}
          {% if import_error %}<p>{{ import_error }}</p>{% endif %}
        </div>
      {% endif %}

      {% if active_source == 'slack' %}
        <div class="import-box {{ 'error' if import_error else '' }}">
          <div class="import-graph">
            (:Person)-[:SENT_SLACK_MESSAGE]-&gt;(:SlackMessage)-[:SLACK_THREAD_REPLY_TO]-&gt;(:SlackMessage)
          </div>
          <form method="post" action="/import/slack">
            <button type="submit">Importera alla Slack-meddelanden</button>
          </form>
          {% if import_result %}<p>{{ import_result }}</p>{% endif %}
          {% if import_error %}<p>{{ import_error }}</p>{% endif %}
        </div>
      {% endif %}

      {% if active_source == 'teams' %}
        <div class="import-box {{ 'error' if import_error else '' }}">
          <div class="import-graph">
            (:Person)-[:PARTICIPATED_IN_MEETING]-&gt;(:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]-&gt;(:TeamsTranscriptSegment)
            <br>
            (:Person)-[:SPOKE_TEAMS_TRANSCRIPT_SEGMENT]-&gt;(:TeamsTranscriptSegment)
          </div>
          <form method="post" action="/import/teams">
            <button type="submit">Importera alla Teams-transkript</button>
          </form>
          {% if import_result %}<p>{{ import_result }}</p>{% endif %}
          {% if import_error %}<p>{{ import_error }}</p>{% endif %}
        </div>
      {% endif %}

      {% if active_source == 'issues' %}
        <div class="import-box {{ 'error' if import_error else '' }}">
          <div class="import-graph">
            (:Person)-[:OWNS_ISSUE]-&gt;(:Issue)
            <br>
            (:Person)-[:COMMENTED_ON_ISSUE]-&gt;(:Issue)
          </div>
          <form method="post" action="/import/issues">
            <button type="submit">Importera alla ärenden</button>
          </form>
          {% if import_result %}<p>{{ import_result }}</p>{% endif %}
          {% if import_error %}<p>{{ import_error }}</p>{% endif %}
        </div>
      {% endif %}

      {% if active_source == 'documents' %}
        <div class="import-box {{ 'error' if import_error else '' }}">
          <div class="import-graph">
            (:Person)-[:AUTHORED_DOCUMENT]-&gt;(:Document)
          </div>
          <form method="post" action="/import/documents">
            <button type="submit">Importera alla dokument</button>
          </form>
          {% if import_result %}<p>{{ import_result }}</p>{% endif %}
          {% if import_error %}<p>{{ import_error }}</p>{% endif %}
        </div>
      {% endif %}

      {% if active_source == 'prs' %}
        <div class="import-box {{ 'error' if import_error else '' }}">
          <div class="import-graph">
            (:Person)-[:AUTHORED_PR]-&gt;(:PullRequest)
            <br>
            (:Person)-[:REVIEWED_PR]-&gt;(:PullRequest)
          </div>
          <form method="post" action="/import/prs">
            <button type="submit">Importera alla PR:er</button>
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


def load_all_slack_rows():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT *
                FROM slack_messages
                ORDER BY sent_at, source_instance, workspace_id, channel_id, message_id, version_number
            """)


def load_all_teams_meetings():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT *
                FROM teams_meetings
                ORDER BY started_at, source_instance, meeting_id
            """)


def load_all_teams_segments():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT *
                FROM teams_transcript_segments
                ORDER BY source_instance, meeting_id, sequence_number
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


def ensure_slack_graph_schema(tx):
    tx.run("""
        CREATE CONSTRAINT slack_message_key IF NOT EXISTS
        FOR (m:SlackMessage)
        REQUIRE (m.source_instance, m.workspace_id, m.channel_id, m.message_id, m.version_number) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT person_key IF NOT EXISTS
        FOR (p:Person)
        REQUIRE p.person_key IS UNIQUE
    """)


def ensure_teams_graph_schema(tx):
    tx.run("""
        CREATE CONSTRAINT teams_meeting_key IF NOT EXISTS
        FOR (m:TeamsMeeting)
        REQUIRE (m.source_instance, m.meeting_id) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT transcript_segment_key IF NOT EXISTS
        FOR (s:TeamsTranscriptSegment)
        REQUIRE (s.source_instance, s.meeting_id, s.segment_id) IS UNIQUE
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
            m.name = $message_id,
            m.display_name = $message_id,
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
            MERGE (p)-[:SENT_MAIL]->(m)
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
            MERGE (m)-[r:MAIL_RECIPIENT]->(p)
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


def import_slack_row(tx, row):
    tx.run("""
        MERGE (m:SlackMessage {
            source_instance: $source_instance,
            workspace_id: $workspace_id,
            channel_id: $channel_id,
            message_id: $message_id,
            version_number: $version_number
        })
        SET m.channel_name = $channel_name,
            m.display_name = $display_name,
            m.name = $display_name,
            m.author_source_id = $author_source_id,
            m.author_name = $author_name,
            m.author_email = $author_email,
            m.body = $body,
            m.sent_at = $sent_at,
            m.version_at = $version_at,
            m.thread_root_id = $thread_root_id,
            m.source_url = $source_url
    """, {
        "source_instance": row["source_instance"],
        "workspace_id": row["workspace_id"],
        "channel_id": row["channel_id"],
        "message_id": row["message_id"],
        "version_number": row["version_number"],
        "channel_name": row["channel_name"],
        "display_name": row["message_id"],
        "author_source_id": row["author_source_id"],
        "author_name": row["author_name"],
        "author_email": row["author_email"],
        "body": row["body"],
        "sent_at": row["sent_at"].isoformat(),
        "version_at": row["version_at"].isoformat(),
        "thread_root_id": row["thread_root_id"],
        "source_url": row["source_url"],
    })

    author_key = person_key(row["author_name"], row["author_email"])
    if author_key:
        tx.run("""
            MATCH (m:SlackMessage {
                source_instance: $source_instance,
                workspace_id: $workspace_id,
                channel_id: $channel_id,
                message_id: $message_id,
                version_number: $version_number
            })
            MERGE (p:Person {person_key: $person_key})
            SET p.name = coalesce($name, p.name),
                p.email = coalesce($email, p.email),
                p.source_id = coalesce($source_id, p.source_id)
            MERGE (p)-[:SENT_SLACK_MESSAGE]->(m)
        """, {
            "source_instance": row["source_instance"],
            "workspace_id": row["workspace_id"],
            "channel_id": row["channel_id"],
            "message_id": row["message_id"],
            "version_number": row["version_number"],
            "person_key": author_key,
            "name": row["author_name"],
            "email": row["author_email"],
            "source_id": row["author_source_id"],
        })

    if row["thread_root_id"] and row["thread_root_id"] != row["message_id"]:
        tx.run("""
            MATCH (reply:SlackMessage {
                source_instance: $source_instance,
                workspace_id: $workspace_id,
                channel_id: $channel_id,
                message_id: $message_id,
                version_number: $version_number
            })
            MATCH (root:SlackMessage {
                source_instance: $source_instance,
                workspace_id: $workspace_id,
                channel_id: $channel_id,
                message_id: $thread_root_id
            })
            MERGE (reply)-[:SLACK_THREAD_REPLY_TO]->(root)
        """, {
            "source_instance": row["source_instance"],
            "workspace_id": row["workspace_id"],
            "channel_id": row["channel_id"],
            "message_id": row["message_id"],
            "version_number": row["version_number"],
            "thread_root_id": row["thread_root_id"],
        })


def import_slack_to_neo4j(uri, user, password, database):
    rows = load_all_slack_rows()
    if not password:
        raise RuntimeError("Neo4j password is required.")

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.execute_write(ensure_slack_graph_schema)
            for row in rows:
                session.execute_write(import_slack_row, row)

            node_counts = session.execute_read(lambda tx: tx.run("""
                MATCH (n)
                WHERE n:SlackMessage OR n:Person
                RETURN labels(n)[0] AS label, count(n) AS count
                ORDER BY label
            """).data())

    counts = ", ".join(f"{item['label']}: {item['count']}" for item in node_counts)
    return f"Importerade {len(rows)} Slack-rader. Grafen innehåller nu {counts}."


def import_teams_meeting_row(tx, row):
    participants = row.get("participants") or []
    participants_raw = json.dumps(participants, ensure_ascii=False)

    tx.run("""
        MERGE (m:TeamsMeeting {
            source_instance: $source_instance,
            meeting_id: $meeting_id
        })
        SET m.name = $meeting_id,
            m.display_name = $meeting_id,
            m.title = $title,
            m.started_at = $started_at,
            m.ended_at = $ended_at,
            m.participants_raw = $participants_raw,
            m.source_url = $source_url
    """, {
        "source_instance": row["source_instance"],
        "meeting_id": row["meeting_id"],
        "title": row["title"],
        "started_at": row["started_at"].isoformat(),
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "participants_raw": participants_raw,
        "source_url": row["source_url"],
    })

    for participant in participants:
        name = participant.get("name")
        email = participant.get("email") or participant.get("address")
        source_id = participant.get("source_id") or participant.get("id")
        key = person_key(name, email) or (f"source:{source_id}" if source_id else None)
        if not key:
            continue
        tx.run("""
            MATCH (m:TeamsMeeting {
                source_instance: $source_instance,
                meeting_id: $meeting_id
            })
            MERGE (p:Person {person_key: $person_key})
            SET p.name = coalesce($name, p.name),
                p.email = coalesce($email, p.email),
                p.source_id = coalesce($source_id, p.source_id)
            MERGE (p)-[:PARTICIPATED_IN_MEETING]->(m)
        """, {
            "source_instance": row["source_instance"],
            "meeting_id": row["meeting_id"],
            "person_key": key,
            "name": name,
            "email": email,
            "source_id": source_id,
        })


def import_teams_segment_row(tx, row):
    tx.run("""
        MATCH (m:TeamsMeeting {
            source_instance: $source_instance,
            meeting_id: $meeting_id
        })
        MERGE (s:TeamsTranscriptSegment {
            source_instance: $source_instance,
            meeting_id: $meeting_id,
            segment_id: $segment_id
        })
        SET s.name = $segment_id,
            s.display_name = $segment_id,
            s.sequence_number = $sequence_number,
            s.speaker_source_id = $speaker_source_id,
            s.speaker_name = $speaker_name,
            s.start_offset_ms = $start_offset_ms,
            s.end_offset_ms = $end_offset_ms,
            s.body = $body
        MERGE (m)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(s)
    """, {
        "source_instance": row["source_instance"],
        "meeting_id": row["meeting_id"],
        "segment_id": row["segment_id"],
        "sequence_number": row["sequence_number"],
        "speaker_source_id": row["speaker_source_id"],
        "speaker_name": row["speaker_name"],
        "start_offset_ms": row["start_offset_ms"],
        "end_offset_ms": row["end_offset_ms"],
        "body": row["body"],
    })

    key = f"source:{row['speaker_source_id']}" if row["speaker_source_id"] else person_key(row["speaker_name"], None)
    if key:
        tx.run("""
            MATCH (s:TeamsTranscriptSegment {
                source_instance: $source_instance,
                meeting_id: $meeting_id,
                segment_id: $segment_id
            })
            MATCH (m:TeamsMeeting {
                source_instance: $source_instance,
                meeting_id: $meeting_id
            })
            OPTIONAL MATCH (existing:Person {source_id: $source_id})
            WITH s, m, existing
            CALL {
                WITH existing
                WITH existing WHERE existing IS NOT NULL
                RETURN existing AS p
                UNION
                WITH existing
                WITH existing WHERE existing IS NULL
                MERGE (created:Person {person_key: $person_key})
                RETURN created AS p
            }
            SET p.name = coalesce($name, p.name),
                p.source_id = coalesce($source_id, p.source_id)
            MERGE (p)-[:SPOKE_TEAMS_TRANSCRIPT_SEGMENT]->(s)
            MERGE (p)-[:PARTICIPATED_IN_MEETING]->(m)
        """, {
            "source_instance": row["source_instance"],
            "meeting_id": row["meeting_id"],
            "segment_id": row["segment_id"],
            "person_key": key,
            "name": row["speaker_name"],
            "source_id": row["speaker_source_id"],
        })


def import_teams_to_neo4j(uri, user, password, database):
    meetings = load_all_teams_meetings()
    segments = load_all_teams_segments()
    if not password:
        raise RuntimeError("Neo4j password is required.")

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.execute_write(ensure_teams_graph_schema)
            for row in meetings:
                session.execute_write(import_teams_meeting_row, row)
            for row in segments:
                session.execute_write(import_teams_segment_row, row)

            node_counts = session.execute_read(lambda tx: tx.run("""
                MATCH (n)
                WHERE n:TeamsMeeting OR n:TeamsTranscriptSegment OR n:Person
                RETURN labels(n)[0] AS label, count(n) AS count
                ORDER BY label
            """).data())

    counts = ", ".join(f"{item['label']}: {item['count']}" for item in node_counts)
    return f"Importerade {len(meetings)} Teams-moten och {len(segments)} transkriptsegment. Grafen innehåller nu {counts}."


def load_all_issue_rows():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT DISTINCT ON (source_instance, issue_id) *
                FROM issue_versions
                ORDER BY source_instance, issue_id, version_number DESC
            """)


def load_all_issue_comments():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT DISTINCT ON (source_instance, comment_id) *
                FROM issue_comments
                ORDER BY source_instance, comment_id, version_number DESC
            """)


def ensure_issue_graph_schema(tx):
    tx.run("""
        CREATE CONSTRAINT issue_node_key IF NOT EXISTS
        FOR (i:Issue)
        REQUIRE (i.source_instance, i.issue_id) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT person_key IF NOT EXISTS
        FOR (p:Person)
        REQUIRE p.person_key IS UNIQUE
    """)


# Relationship types are inlined in Cypher, so only these fixed values are allowed.
ISSUE_PERSON_RELATIONSHIPS = {"OWNS_ISSUE", "COMMENTED_ON_ISSUE"}


def issue_person_key(name, source_id):
    if source_id:
        return f"source:{source_id}"
    return person_key(name, None)


def merge_issue_person(tx, row, relationship, name, source_id):
    """Attach a person to an issue, reusing an existing Person with the same source_id."""
    if relationship not in ISSUE_PERSON_RELATIONSHIPS:
        raise ValueError(f"Unsupported issue relationship: {relationship}")

    key = issue_person_key(name, source_id)
    if not key:
        return

    tx.run(f"""
        MATCH (i:Issue {{
            source_instance: $source_instance,
            issue_id: $issue_id
        }})
        OPTIONAL MATCH (existing:Person {{source_id: $source_id}})
        WITH i, existing
        CALL (existing) {{
            WITH existing WHERE existing IS NOT NULL
            RETURN existing AS p
            UNION
            WITH existing WHERE existing IS NULL
            MERGE (created:Person {{person_key: $person_key}})
            RETURN created AS p
        }}
        SET p.name = coalesce($name, p.name),
            p.source_id = coalesce($source_id, p.source_id)
        MERGE (p)-[:{relationship}]->(i)
    """, {
        "source_instance": row["source_instance"],
        "issue_id": row["issue_id"],
        "person_key": key,
        "name": name,
        "source_id": source_id,
    })


def issue_comment_entry(comment):
    return {
        "comment_id": comment["comment_id"],
        "version_number": comment["version_number"],
        "author_name": comment["author_name"],
        "author_source_id": comment["author_source_id"],
        "body": comment["body"],
        "created_at": comment["created_at"].isoformat(),
        "reply_to_comment_id": comment["reply_to_comment_id"],
        "source_url": comment["source_url"],
    }


def import_issue_row(tx, row, comments):
    """Import one issue. Comments are stored on the Issue node instead of separate nodes."""
    comments_raw = json.dumps([issue_comment_entry(comment) for comment in comments], ensure_ascii=False)

    tx.run("""
        MERGE (i:Issue {
            source_instance: $source_instance,
            issue_id: $issue_id
        })
        SET i.name = $issue_key,
            i.display_name = $issue_key,
            i.issue_key = $issue_key,
            i.issue_type = $issue_type,
            i.title = $title,
            i.description = $description,
            i.acceptance_criteria = $acceptance_criteria,
            i.status = $status,
            i.priority = $priority,
            i.creator_name = $creator_name,
            i.creator_source_id = $creator_source_id,
            i.assignee_name = $assignee_name,
            i.assignee_source_id = $assignee_source_id,
            i.created_at = $created_at,
            i.version_at = $version_at,
            i.version_number = $version_number,
            i.comment_count = $comment_count,
            i.comments_raw = $comments_raw,
            i.source_url = $source_url
    """, {
        "source_instance": row["source_instance"],
        "issue_id": row["issue_id"],
        "issue_key": row["issue_key"],
        "issue_type": row["issue_type"],
        "title": row["title"],
        "description": row["description"],
        "acceptance_criteria": row["acceptance_criteria"],
        "status": row["status"],
        "priority": row["priority"],
        "creator_name": row["creator_name"],
        "creator_source_id": row["creator_source_id"],
        "assignee_name": row["assignee_name"],
        "assignee_source_id": row["assignee_source_id"],
        "created_at": row["created_at"].isoformat(),
        "version_at": row["version_at"].isoformat(),
        "version_number": row["version_number"],
        "comment_count": len(comments),
        "comments_raw": comments_raw,
        "source_url": row["source_url"],
    })

    owner_name = row["assignee_name"] or row["creator_name"]
    owner_source_id = row["assignee_source_id"] or row["creator_source_id"]
    merge_issue_person(tx, row, "OWNS_ISSUE", owner_name, owner_source_id)

    authors = {}
    for comment in comments:
        key = issue_person_key(comment["author_name"], comment["author_source_id"])
        if not key:
            continue
        authors.setdefault(key, (comment["author_name"], comment["author_source_id"]))

    for name, source_id in authors.values():
        merge_issue_person(tx, row, "COMMENTED_ON_ISSUE", name, source_id)


def import_issues_to_neo4j(uri, user, password, database):
    issues = load_all_issue_rows()
    comments = load_all_issue_comments()
    if not password:
        raise RuntimeError("Neo4j password is required.")

    comments_by_issue = {}
    for comment in comments:
        key = (comment["source_instance"], comment["issue_id"])
        comments_by_issue.setdefault(key, []).append(comment)
    for issue_comments in comments_by_issue.values():
        issue_comments.sort(key=lambda comment: (comment["created_at"], comment["comment_id"]))

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.execute_write(ensure_issue_graph_schema)
            for row in issues:
                key = (row["source_instance"], row["issue_id"])
                session.execute_write(import_issue_row, row, comments_by_issue.get(key, []))

            node_counts = session.execute_read(lambda tx: tx.run("""
                MATCH (n)
                WHERE n:Issue OR n:Person
                RETURN labels(n)[0] AS label, count(n) AS count
                ORDER BY label
            """).data())

    counts = ", ".join(f"{item['label']}: {item['count']}" for item in node_counts)
    return f"Importerade {len(issues)} arenden och {len(comments)} kommentarer. Grafen innehåller nu {counts}."


def load_all_document_rows():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT *
                FROM document_versions
                ORDER BY source_instance, document_id, version_number
            """)


def ensure_document_graph_schema(tx):
    tx.run("""
        CREATE CONSTRAINT document_node_key IF NOT EXISTS
        FOR (d:Document)
        REQUIRE (d.source_instance, d.document_id) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT person_key IF NOT EXISTS
        FOR (p:Person)
        REQUIRE p.person_key IS UNIQUE
    """)


def document_version_entry(row):
    return {
        "version_number": row["version_number"],
        "document_type": row["document_type"],
        "title": row["title"],
        "body": row["body"],
        "content_format": row["content_format"],
        "author_name": row["author_name"],
        "author_source_id": row["author_source_id"],
        "version_at": row["version_at"].isoformat(),
        "change_summary": row["change_summary"],
        "source_url": row["source_url"],
    }


def import_document_row(tx, latest, earlier_versions):
    """Import one document. Earlier versions are stored on the Document node."""
    versions_raw = json.dumps([document_version_entry(row) for row in earlier_versions], ensure_ascii=False)

    tx.run("""
        MERGE (d:Document {
            source_instance: $source_instance,
            document_id: $document_id
        })
        SET d.name = $document_id,
            d.display_name = $document_id,
            d.document_type = $document_type,
            d.title = $title,
            d.body = $body,
            d.content_format = $content_format,
            d.author_name = $author_name,
            d.author_source_id = $author_source_id,
            d.created_at = $created_at,
            d.version_at = $version_at,
            d.version_number = $version_number,
            d.change_summary = $change_summary,
            d.versions_raw = $versions_raw,
            d.source_url = $source_url
    """, {
        "source_instance": latest["source_instance"],
        "document_id": latest["document_id"],
        "document_type": latest["document_type"],
        "title": latest["title"],
        "body": latest["body"],
        "content_format": latest["content_format"],
        "author_name": latest["author_name"],
        "author_source_id": latest["author_source_id"],
        "created_at": latest["created_at"].isoformat(),
        "version_at": latest["version_at"].isoformat(),
        "version_number": latest["version_number"],
        "change_summary": latest["change_summary"],
        "versions_raw": versions_raw,
        "source_url": latest["source_url"],
    })

    author_source_id = latest["author_source_id"]
    key = f"source:{author_source_id}" if author_source_id else person_key(latest["author_name"], None)
    if not key:
        return

    tx.run("""
        MATCH (d:Document {
            source_instance: $source_instance,
            document_id: $document_id
        })
        OPTIONAL MATCH (existing:Person {source_id: $source_id})
        WITH d, existing
        CALL (existing) {
            WITH existing WHERE existing IS NOT NULL
            RETURN existing AS p
            UNION
            WITH existing WHERE existing IS NULL
            MERGE (created:Person {person_key: $person_key})
            RETURN created AS p
        }
        SET p.name = coalesce($name, p.name),
            p.source_id = coalesce($source_id, p.source_id)
        MERGE (p)-[:AUTHORED_DOCUMENT]->(d)
    """, {
        "source_instance": latest["source_instance"],
        "document_id": latest["document_id"],
        "person_key": key,
        "name": latest["author_name"],
        "source_id": author_source_id,
    })


def import_documents_to_neo4j(uri, user, password, database):
    rows = load_all_document_rows()
    if not password:
        raise RuntimeError("Neo4j password is required.")

    versions_by_document = {}
    for row in rows:
        key = (row["source_instance"], row["document_id"])
        versions_by_document.setdefault(key, []).append(row)

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.execute_write(ensure_document_graph_schema)
            for versions in versions_by_document.values():
                versions.sort(key=lambda row: row["version_number"])
                session.execute_write(import_document_row, versions[-1], versions[:-1])

            node_counts = session.execute_read(lambda tx: tx.run("""
                MATCH (n)
                WHERE n:Document OR n:Person
                RETURN labels(n)[0] AS label, count(n) AS count
                ORDER BY label
            """).data())

    counts = ", ".join(f"{item['label']}: {item['count']}" for item in node_counts)
    return f"Importerade {len(versions_by_document)} dokument och {len(rows)} versioner. Grafen innehåller nu {counts}."


def load_all_pr_rows():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT *
                FROM pr_versions
                ORDER BY source_instance, repository, pr_number, version_number
            """)


def load_all_pr_reviews():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            return query_all(cur, """
                SELECT DISTINCT ON (source_instance, repository, pr_number, source_id) *
                FROM pr_reviews
                ORDER BY source_instance, repository, pr_number, source_id, version_number DESC
            """)


def ensure_pr_graph_schema(tx):
    tx.run("""
        CREATE CONSTRAINT pull_request_key IF NOT EXISTS
        FOR (pr:PullRequest)
        REQUIRE (pr.source_instance, pr.repository, pr.pr_number) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT person_key IF NOT EXISTS
        FOR (p:Person)
        REQUIRE p.person_key IS UNIQUE
    """)


# Relationship types are inlined in Cypher, so only these fixed values are allowed.
PR_PERSON_RELATIONSHIPS = {"AUTHORED_PR", "REVIEWED_PR"}


def pr_person_key(name, source_id):
    if source_id:
        return f"source:{source_id}"
    return person_key(name, None)


def merge_pr_person(tx, row, relationship, name, source_id):
    """Attach a person to a pull request, reusing an existing Person with the same source_id."""
    if relationship not in PR_PERSON_RELATIONSHIPS:
        raise ValueError(f"Unsupported pull request relationship: {relationship}")

    key = pr_person_key(name, source_id)
    if not key:
        return

    tx.run(f"""
        MATCH (pr:PullRequest {{
            source_instance: $source_instance,
            repository: $repository,
            pr_number: $pr_number
        }})
        OPTIONAL MATCH (existing:Person {{source_id: $source_id}})
        WITH pr, existing
        CALL (existing) {{
            WITH existing WHERE existing IS NOT NULL
            RETURN existing AS p
            UNION
            WITH existing WHERE existing IS NULL
            MERGE (created:Person {{person_key: $person_key}})
            RETURN created AS p
        }}
        SET p.name = coalesce($name, p.name),
            p.source_id = coalesce($source_id, p.source_id)
        MERGE (p)-[:{relationship}]->(pr)
    """, {
        "source_instance": row["source_instance"],
        "repository": row["repository"],
        "pr_number": row["pr_number"],
        "person_key": key,
        "name": name,
        "source_id": source_id,
    })


def pr_review_entry(review):
    return {
        "source_id": review["source_id"],
        "version_number": review["version_number"],
        "pr_version_number": review["pr_version_number"],
        "entry_type": review["entry_type"],
        "author_name": review["author_name"],
        "author_source_id": review["author_source_id"],
        "body": review["body"],
        "created_at": review["created_at"].isoformat(),
        "version_at": review["version_at"].isoformat(),
        "reviewed_commit": review["reviewed_commit"],
        "reply_to_source_id": review["reply_to_source_id"],
        "review_group_id": review["review_group_id"],
        "file_path": review["file_path"],
        "line_number": review["line_number"],
        "diff_side": review["diff_side"],
        "source_url": review["source_url"],
    }


def import_pr_row(tx, latest, reviews):
    """Import one pull request. Reviews are stored on the PullRequest node."""
    reviews_raw = json.dumps([pr_review_entry(review) for review in reviews], ensure_ascii=False)
    code_changes_raw = json.dumps(latest["code_changes"] or [], ensure_ascii=False)
    display_name = f'{latest["repository"]}#{latest["pr_number"]}'

    tx.run("""
        MERGE (pr:PullRequest {
            source_instance: $source_instance,
            repository: $repository,
            pr_number: $pr_number
        })
        SET pr.name = $display_name,
            pr.display_name = $display_name,
            pr.title = $title,
            pr.description = $description,
            pr.author_name = $author_name,
            pr.author_source_id = $author_source_id,
            pr.state = $state,
            pr.created_at = $created_at,
            pr.version_at = $version_at,
            pr.version_number = $version_number,
            pr.base_commit = $base_commit,
            pr.head_commit = $head_commit,
            pr.code_changes_raw = $code_changes_raw,
            pr.reviews_raw = $reviews_raw,
            pr.source_url = $source_url
    """, {
        "source_instance": latest["source_instance"],
        "repository": latest["repository"],
        "pr_number": latest["pr_number"],
        "display_name": display_name,
        "title": latest["title"],
        "description": latest["description"],
        "author_name": latest["author_name"],
        "author_source_id": latest["author_source_id"],
        "state": latest["state"],
        "created_at": latest["created_at"].isoformat(),
        "version_at": latest["version_at"].isoformat(),
        "version_number": latest["version_number"],
        "base_commit": latest["base_commit"],
        "head_commit": latest["head_commit"],
        "code_changes_raw": code_changes_raw,
        "reviews_raw": reviews_raw,
        "source_url": latest["source_url"],
    })

    merge_pr_person(tx, latest, "AUTHORED_PR", latest["author_name"], latest["author_source_id"])

    reviewers = {}
    for review in reviews:
        key = pr_person_key(review["author_name"], review["author_source_id"])
        if not key:
            continue
        reviewers.setdefault(key, (review["author_name"], review["author_source_id"]))

    for name, source_id in reviewers.values():
        merge_pr_person(tx, latest, "REVIEWED_PR", name, source_id)


def import_prs_to_neo4j(uri, user, password, database):
    rows = load_all_pr_rows()
    reviews = load_all_pr_reviews()
    if not password:
        raise RuntimeError("Neo4j password is required.")

    versions_by_pr = {}
    for row in rows:
        key = (row["source_instance"], row["repository"], row["pr_number"])
        versions_by_pr.setdefault(key, []).append(row)

    reviews_by_pr = {}
    for review in reviews:
        key = (review["source_instance"], review["repository"], review["pr_number"])
        reviews_by_pr.setdefault(key, []).append(review)
    for pr_reviews in reviews_by_pr.values():
        pr_reviews.sort(key=lambda review: (review["created_at"], review["source_id"]))

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.execute_write(ensure_pr_graph_schema)
            for key, versions in versions_by_pr.items():
                versions.sort(key=lambda row: row["version_number"])
                session.execute_write(import_pr_row, versions[-1], reviews_by_pr.get(key, []))

            node_counts = session.execute_read(lambda tx: tx.run("""
                MATCH (n)
                WHERE n:PullRequest OR n:Person
                RETURN labels(n)[0] AS label, count(n) AS count
                ORDER BY label
            """).data())

    counts = ", ".join(f"{item['label']}: {item['count']}" for item in node_counts)
    return f"Importerade {len(versions_by_pr)} PR:er och {len(reviews)} granskningar. Grafen innehåller nu {counts}."


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
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        result = import_mail_to_neo4j(uri, user, password, database)
        return redirect(url_for("index", source="mail", import_result=result))
    except Exception as error:
        return redirect(url_for("index", source="mail", import_error=str(error)))


@app.post("/import/slack")
def import_slack():
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        result = import_slack_to_neo4j(uri, user, password, database)
        return redirect(url_for("index", source="slack", import_result=result))
    except Exception as error:
        return redirect(url_for("index", source="slack", import_error=str(error)))


@app.post("/import/teams")
def import_teams():
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        result = import_teams_to_neo4j(uri, user, password, database)
        return redirect(url_for("index", source="teams", import_result=result))
    except Exception as error:
        return redirect(url_for("index", source="teams", import_error=str(error)))


@app.post("/import/issues")
def import_issues():
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        result = import_issues_to_neo4j(uri, user, password, database)
        return redirect(url_for("index", source="issues", import_result=result))
    except Exception as error:
        return redirect(url_for("index", source="issues", import_error=str(error)))


@app.post("/import/documents")
def import_documents():
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        result = import_documents_to_neo4j(uri, user, password, database)
        return redirect(url_for("index", source="documents", import_result=result))
    except Exception as error:
        return redirect(url_for("index", source="documents", import_error=str(error)))


@app.post("/import/prs")
def import_prs():
    uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    try:
        result = import_prs_to_neo4j(uri, user, password, database)
        return redirect(url_for("index", source="prs", import_result=result))
    except Exception as error:
        return redirect(url_for("index", source="prs", import_error=str(error)))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
