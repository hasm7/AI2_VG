"""Layer specialists. Each one walks its own layers' relationships from the entry points, in code, and returns a
bounded evidence packet. No model call in step 1 (a follow-up call per specialist comes later).

A specialist collects candidate node ids with a priority (0 = the entry point itself, 1 = one step away, 2 = further),
keeps the best `max_nodes_per_specialist`, and returns their embedded texts, which already carry each node's context
from every layer (causal lines, root causes, dependencies, experts, bus factor, ...).
"""

import time

from .db import NODE_NAME, fetch_nodes, node_text, read

SOURCE_LABELS = [
    "MailMessage", "SlackMessage", "TeamsTranscriptSegment", "IssueVersion", "IssueComment",
    "DocumentVersion", "PullRequest", "PullRequestReview", "CodeChange",
]

PERSON_ACTIVITY_TYPES = [
    "SENT_MAIL", "SENT_SLACK_MESSAGE", "SPOKE_TEAMS_TRANSCRIPT_SEGMENT", "AUTHORED_PR", "WROTE_PR_REVIEW",
    "WROTE_ISSUE_COMMENT", "CHANGED_ISSUE_VERSION", "AUTHORED_DOCUMENT_VERSION", "AUTHORED_DOCUMENT",
    "CREATED_ISSUE", "OWNS_ISSUE", "COMMENTED_ON_ISSUE",
]

# (priority, query). Every query takes `$ids` (entry point element ids) and returns `id`.
SOURCES_QUERIES = [
    (0, "MATCH (n) WHERE elementId(n) IN $ids AND (any(l IN labels(n) WHERE l IN $labels) OR n:Issue OR n:Document) "
        "RETURN elementId(n) AS id"),
    (1, "MATCH (p) WHERE elementId(p) IN $ids AND (p:Issue OR p:Document OR p:PullRequest) "
        "MATCH (p)-[:HAS_ISSUE_VERSION|HAS_ISSUE_COMMENT|HAS_DOCUMENT_VERSION|HAS_PR_REVIEW|HAS_CODE_CHANGE]->(c:Searchable) "
        "RETURN elementId(c) AS id"),
    (1, "MATCH (e:Event) WHERE elementId(e) IN $ids MATCH (e)-[:EVIDENCED_BY]->(s:Searchable) RETURN elementId(s) AS id"),
    (2, "MATCH (p) WHERE elementId(p) IN $ids AND (p:Issue OR p:Document OR p:PullRequest) "
        "MATCH (s:Searchable)-[r:MENTIONS_ISSUE|MENTIONS_PULL_REQUEST|MENTIONS_DOCUMENT]->(p) "
        "WHERE r.extracted_by = 'reference-extraction-v1' RETURN elementId(s) AS id"),
]

CAUSES_QUERIES = [
    (0, "MATCH (n) WHERE elementId(n) IN $ids AND (n:Event OR n:RootCause OR n:Topic) RETURN elementId(n) AS id"),
    (1, "MATCH (s) WHERE elementId(s) IN $ids MATCH (e:Event)-[:EVIDENCED_BY]->(s) RETURN elementId(e) AS id"),
    (1, "MATCH (e:Event) WHERE elementId(e) IN $ids MATCH (e)-[:HAS_ROOT_CAUSE]->(r:RootCause) RETURN elementId(r) AS id"),
    (1, "MATCH (r:RootCause) WHERE elementId(r) IN $ids MATCH (e:Event)-[:HAS_ROOT_CAUSE]->(r) RETURN elementId(e) AS id"),
    (1, "MATCH (c:CodeChange) WHERE elementId(c) IN $ids MATCH (c)-[:CONTRIBUTED_TO]->(e:Event) RETURN elementId(e) AS id"),
    (1, "MATCH (c:Component) WHERE elementId(c) IN $ids MATCH (r:RootCause)-[:ROOT_CAUSE_IN_COMPONENT]->(c) "
        "RETURN elementId(r) AS id"),
    (2, "MATCH (x) WHERE elementId(x) IN $ids AND (x:Issue OR x:IssueVersion OR x:IssueComment) "
        "MATCH (i:Issue) WHERE i = x OR (i)-[:HAS_ISSUE_VERSION|HAS_ISSUE_COMMENT]->(x) "
        "MATCH (i)-[:ABOUT_TOPIC]->(:Topic)<-[:EVENT_OF_TOPIC]-(e:Event) RETURN elementId(e) AS id"),
    (2, "MATCH (c:Component) WHERE elementId(c) IN $ids MATCH (e:Event)-[:AFFECTED_COMPONENT]->(c) RETURN elementId(e) AS id"),
]

ARCHITECTURE_QUERIES = [
    (0, "MATCH (c:Component) WHERE elementId(c) IN $ids RETURN elementId(c) AS id"),
    (1, "MATCH (e:Event) WHERE elementId(e) IN $ids MATCH (e)-[:AFFECTED_COMPONENT]->(c:Component) RETURN elementId(c) AS id"),
    (1, "MATCH (r:RootCause) WHERE elementId(r) IN $ids MATCH (r)-[:ROOT_CAUSE_IN_COMPONENT]->(c:Component) "
        "RETURN elementId(c) AS id"),
    (1, "MATCH (s) WHERE elementId(s) IN $ids MATCH (c:Component)-[:COMPONENT_EVIDENCED_BY]->(s) RETURN elementId(c) AS id"),
    (1, "MATCH (cc:CodeChange) WHERE elementId(cc) IN $ids "
        "MATCH (cc)-[:MODIFIES_FILE]->(:File)<-[:IMPLEMENTED_IN]-(c:Component) RETURN elementId(c) AS id"),
    (2, "MATCH (x) WHERE elementId(x) IN $ids AND (x:PullRequest OR x:PullRequestReview) "
        "MATCH (pr:PullRequest) WHERE pr = x OR (pr)-[:HAS_PR_REVIEW]->(x) "
        "MATCH (pr)-[:HAS_CODE_CHANGE]->(:CodeChange)-[:MODIFIES_FILE]->(:File)<-[:IMPLEMENTED_IN]-(c:Component) "
        "RETURN elementId(c) AS id"),
    (2, "MATCH (s) WHERE elementId(s) IN $ids "
        "MATCH (e:Event)-[:EVIDENCED_BY]->(s) MATCH (e)-[:AFFECTED_COMPONENT]->(c:Component) RETURN elementId(c) AS id"),
    (2, "MATCH (c0:Component) WHERE elementId(c0) IN $ids MATCH (c0)-[:DEPENDS_ON]-(c:Component) RETURN elementId(c) AS id"),
]

# Only eligible persons carry the `Searchable` label (mailbox and ambiguous identities are left out).
PEOPLE_QUERIES = [
    (0, "MATCH (n) WHERE elementId(n) IN $ids AND ((n:Person AND n:Searchable) OR n:Community) RETURN elementId(n) AS id"),
    (1, "MATCH (e:Event) WHERE elementId(e) IN $ids MATCH (p:Person:Searchable)-[:ACTED_IN_EVENT]->(e) RETURN elementId(p) AS id"),
    (1, "MATCH (s) WHERE elementId(s) IN $ids MATCH (p:Person:Searchable)-[r]->(s) WHERE type(r) IN $activity "
        "RETURN elementId(p) AS id"),
    (1, "MATCH (s) WHERE elementId(s) IN $ids "
        "MATCH (p:Person:Searchable)-[:HAS_EXPERTISE]->(:Expertise)-[:EXPERTISE_IN]->(s) RETURN elementId(p) AS id"),
    (1, "MATCH (c:Community) WHERE elementId(c) IN $ids MATCH (p:Person:Searchable)-[:MEMBER_OF_COMMUNITY]->(c) "
        "RETURN elementId(p) AS id"),
    (1, "MATCH (x) WHERE elementId(x) IN $ids AND (x:TeamsMeeting OR x:TeamsTranscriptSegment) "
        "MATCH (m:TeamsMeeting) WHERE m = x OR (m)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(x) "
        "MATCH (p:Person:Searchable)-[:PARTICIPATED_IN_MEETING]->(m) RETURN elementId(p) AS id"),
    (1, "MATCH (mail:MailMessage) WHERE elementId(mail) IN $ids "
        "MATCH (mail)-[:MAIL_RECIPIENT]->(p:Person:Searchable) RETURN elementId(p) AS id"),
    (2, "MATCH (i:Issue) WHERE elementId(i) IN $ids "
        "MATCH (i)-[:HAS_ISSUE_VERSION|HAS_ISSUE_COMMENT]->(x)<-[r]-(p:Person:Searchable) WHERE type(r) IN $activity "
        "RETURN elementId(p) AS id"),
]

# Who took part: meeting participants and mail recipients are relationships, not part of any person's text, so they
# are added as their own evidence item on the meeting or mail node (citable). Every person is listed here, including
# mailboxes and ambiguous names, since this is a record of who took part.
PARTICIPATION = f"""
MATCH (x) WHERE elementId(x) IN $ids AND (x:TeamsMeeting OR x:TeamsTranscriptSegment)
MATCH (n:TeamsMeeting) WHERE n = x OR (n)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(x)
MATCH (p:Person)-[:PARTICIPATED_IN_MEETING]->(n)
WITH n, collect(DISTINCT p.name) AS names
RETURN elementId(n) AS id, 'TeamsMeeting' AS label, {NODE_NAME} AS name, toString(n.started_at) AS at,
       'Meeting "' + n.title + '", participants: ' + reduce(s = '', name IN names |
       s + CASE WHEN s = '' THEN '' ELSE ', ' END + name) AS text
UNION
MATCH (n:MailMessage) WHERE elementId(n) IN $ids
MATCH (n)-[:MAIL_RECIPIENT]->(p:Person)
OPTIONAL MATCH (sender:Person)-[:SENT_MAIL]->(n)
WITH n, sender, collect(DISTINCT p.name) AS names
RETURN elementId(n) AS id, 'MailMessage' AS label, {NODE_NAME} AS name, toString(n.sent_at) AS at,
       'Mail "' + coalesce(n.subject, '') + '" from ' + coalesce(sender.name, '?') + ' to: '
       + reduce(s = '', name IN names | s + CASE WHEN s = '' THEN '' ELSE ', ' END + name) AS text
"""

RANKING_PERSONS = """
MATCH (p:Person:Searchable)
RETURN p.name AS person, p.collab_weighted_degree AS weighted_degree, p.collab_betweenness AS betweenness,
       p.community_id AS community
ORDER BY betweenness DESC, weighted_degree DESC
"""

RANKING_SUBJECTS = """
MATCH (s) WHERE (s:Topic OR s:Component) AND s.bus_factor IS NOT NULL
RETURN [l IN labels(s) WHERE l <> 'Searchable'][0] AS kind, s.name AS subject, s.bus_factor AS bus_factor,
       s.expert_count AS experts, s.top_expert AS top_expert
ORDER BY bus_factor ASC, subject
"""


def _collect(session, queries: list[tuple[int, str]], params: dict, timeout: float) -> dict[str, int]:
    """Candidate node id -> best (lowest) priority."""
    priorities: dict[str, int] = {}
    for priority, query in queries:
        for row in read(session, query, params, timeout):
            if row["id"] not in priorities or priority < priorities[row["id"]]:
                priorities[row["id"]] = priority
    return priorities


def _packet(session, name: str, queries: list, entry_ids: list[str], settings: dict, extra_params: dict | None = None) -> dict:
    limits = settings["evidence"]
    timeout = settings["query_timeout_seconds"]
    priorities = _collect(session, queries, {"ids": entry_ids, **(extra_params or {})}, timeout)
    rows = fetch_nodes(session, list(priorities), timeout)

    # Best priority first, then by time; the kept nodes are then listed in time order.
    ranked = sorted(rows.values(), key=lambda r: (priorities[r["id"]], r.get("at") or "9999", r["name"] or ""))
    kept = ranked[: limits["max_nodes_per_specialist"]]
    kept.sort(key=lambda r: (r.get("at") or "9999", r["name"] or ""))

    nodes = [
        {
            "id": row["id"],
            "label": row["label"],
            "name": row["name"],
            "at": row.get("at"),
            "priority": priorities[row["id"]],
            "text": node_text(row, limits["max_text_chars"]),
        }
        for row in kept
    ]
    return {"specialist": name, "nodes": nodes, "facts": [], "candidates": len(priorities)}


def sources(session, entry_ids, plan, settings):
    return _packet(session, "sources", SOURCES_QUERIES, entry_ids, settings, {"labels": SOURCE_LABELS})


def causes(session, entry_ids, plan, settings):
    return _packet(session, "causes", CAUSES_QUERIES, entry_ids, settings)


def architecture(session, entry_ids, plan, settings):
    return _packet(session, "architecture", ARCHITECTURE_QUERIES, entry_ids, settings)


def people(session, entry_ids, plan, settings):
    packet = _packet(session, "people", PEOPLE_QUERIES, entry_ids, settings, {"activity": PERSON_ACTIVITY_TYPES})
    timeout = settings["query_timeout_seconds"]
    for row in read(session, PARTICIPATION, {"ids": entry_ids}, timeout):
        packet["nodes"].append({**row, "priority": 1})
    # Ranking questions are answered from the metric properties directly, not from search.
    if "ranking" in plan.get("question_types", []):
        persons = read(session, RANKING_PERSONS, None, timeout)
        subjects = read(session, RANKING_SUBJECTS, None, timeout)
        packet["facts"].append(
            "Collaboration metrics per person (highest betweenness first): "
            + "; ".join(f"{r['person']}: betweenness {r['betweenness']}, weighted degree {r['weighted_degree']}, "
                        f"community {r['community']}" for r in persons)
        )
        packet["facts"].append(
            "Bus factor per topic and component (lowest first): "
            + "; ".join(f"{r['subject']} ({r['kind']}): bus factor {r['bus_factor']}, {r['experts']} experts, "
                        f"top expert {r['top_expert']}" for r in subjects)
        )
    return packet


SPECIALIST_FUNCTIONS = {"sources": sources, "causes": causes, "architecture": architecture, "people": people}


def run_specialist(name: str, driver, database: str, entry_ids: list[str], plan: dict, settings: dict,
                   followup=None) -> tuple[dict, list[dict]]:
    """Runs one specialist in its own read session (specialists run in parallel; a session is not thread-safe).

    `followup(session, packet) -> usage entries`, when given, extends the packet in the same session. Returns
    (packet, usage). The session is closed when the `with` block ends, also on an error.
    """
    started = time.perf_counter()
    usage: list[dict] = []
    try:
        with driver.session(database=database, default_access_mode="READ") as session:
            packet = SPECIALIST_FUNCTIONS[name](session, entry_ids, plan, settings)
            packet["errors"] = []
            if followup is not None:
                try:
                    usage = followup(session, packet)
                except Exception as error:  # noqa: BLE001 - a failed follow-up only means no extra evidence
                    packet["followup"] = [f"follow-up failed: {error}"]
    except Exception as error:  # noqa: BLE001 - reported in the packet and in state, never silently dropped
        packet = {"specialist": name, "nodes": [], "facts": [], "candidates": 0, "errors": [str(error)]}
    packet["ms"] = round((time.perf_counter() - started) * 1000)
    return packet, usage
