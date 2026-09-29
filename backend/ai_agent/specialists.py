"""Layer specialists. Each one walks its own layers' relationships from the entry points, in code, and returns a
bounded evidence packet. No model call in step 1 (a follow-up call per specialist comes later).

A specialist collects candidate node ids with a priority (0 = the entry point itself, 1 = one step away, 2 = further),
keeps the best `max_nodes_per_specialist`, and returns their embedded texts, which already carry each node's context
from every layer (causal lines, root causes, dependencies, experts, bus factor, ...).
"""

import time

from collaboration_layer import ACTIVITY_WEIGHTS

from .db import NODE_NAME, chunk_texts, fetch_nodes, node_text, read, similarities

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

# What a person's expertise on a topic or component rests on: the activity nodes the Expertise layer counted
# (EXPERTISE_EVIDENCED_BY) and how the person is linked to each, for the experts ranked `$max_rank` or better.
# `$ids` limits it to some subjects; null means every subject. Read only; the layer's own relationships.
EXPERTISE_BASIS = """
MATCH (p:Person)-[:HAS_EXPERTISE]->(x:Expertise)-[:EXPERTISE_IN]->(s)
WHERE ($ids IS NULL OR elementId(s) IN $ids) AND x.rank <= $max_rank
OPTIONAL MATCH (x)-[:EXPERTISE_EVIDENCED_BY]->(a)
OPTIONAL MATCH (p)-[r]->(a) WHERE type(r) IN $types
WITH p, x, s, a, r
ORDER BY toString(coalesce(a.occurred_at, a.version_at, a.sent_at, a.created_at))
RETURN p.name AS person, coalesce(s.name, s.display_name) AS subject, x.share AS share, x.rank AS rank,
       collect({how: type(r), name: coalesce(a.display_name, a.name)}) AS activities
ORDER BY subject, rank
"""

# How each counted kind of activity is said in an expertise basis (the kinds in the Expertise layer's weights).
ACTIVITY_PHRASES = {
    "AUTHORED_PR": "wrote pull request",
    "WROTE_PR_REVIEW": "reviewed",
    "AUTHORED_DOCUMENT_VERSION": "wrote document version",
    "ACTED_IN_EVENT": "took part in event",
    "CHANGED_ISSUE_VERSION": "changed issue",
    "WROTE_ISSUE_COMMENT": "commented on issue",
    "SENT_SLACK_MESSAGE": "wrote Slack message",
    "SENT_MAIL": "sent mail",
    "SPOKE_TEAMS_TRANSCRIPT_SEGMENT": "spoke in meeting segment",
}
# Every person in the graph, for a general "who is in the project" question: search finds only the persons most
# similar to the question (at most `entry_points`), so without this list the answer could miss someone by chance and
# still state a total. Per person: e-mail (a different domain usually means another organisation, such as a
# customer), community, what they did counted per kind, and the subjects of mails they sent (often their role, such as
# reporting a problem). Mailboxes and name-only identities that could be several persons are listed apart. Read only.
PERSON_ROSTER = """
MATCH (p:Person)
OPTIONAL MATCH (p)-[r]->(a) WHERE type(r) IN $types
WITH p, type(r) AS how, count(DISTINCT a) AS n
WITH p, collect(CASE WHEN how IS NULL THEN null ELSE {how: how, n: n} END) AS activity
OPTIONAL MATCH (p)<-[:MAIL_RECIPIENT]-(received:MailMessage)
WITH p, activity, count(DISTINCT received) AS received
OPTIONAL MATCH (p)-[:SENT_MAIL]->(sent:MailMessage)
WITH p, activity, received, sent ORDER BY sent.sent_at
RETURN p.name AS name, p.emails AS emails, p.actor_type AS actor_type,
       coalesce(p.identity_ambiguous, false) AS ambiguous, p.community_id AS community, activity, received,
       [subject IN collect(DISTINCT sent.subject) WHERE subject IS NOT NULL][..2] AS mail_subjects
ORDER BY name
"""

# How each kind of activity is counted in the roster.
ROSTER_PHRASES = {
    "AUTHORED_PR": "pull requests written",
    "WROTE_PR_REVIEW": "pull request reviews",
    "AUTHORED_DOCUMENT_VERSION": "document versions written",
    "CREATED_ISSUE": "issues created",
    "OWNS_ISSUE": "issues owned",
    "CHANGED_ISSUE_VERSION": "issue changes",
    "WROTE_ISSUE_COMMENT": "issue comments",
    "SENT_SLACK_MESSAGE": "Slack messages",
    "SENT_MAIL": "mails sent",
    "PARTICIPATED_IN_MEETING": "meetings attended",
    "SPOKE_TEAMS_TRANSCRIPT_SEGMENT": "meeting segments spoken",
    "ACTED_IN_EVENT": "events",
}
MAX_ROSTER = 30  # persons listed, then "... and N more"


def person_roster(session, timeout: float) -> str:
    """One fact listing every person in the graph, e.g. "All persons in the graph (5 people): Anna Berg
    (anna.berg@example.com, community collab-2): issues created: 1, mails sent: 2, ...; ... Not counted as people:
    Support ..."."""
    rows = read(session, PERSON_ROSTER, {"types": list(ROSTER_PHRASES)}, timeout)
    people, apart = [], []
    for row in rows:
        counts = {item["how"]: item["n"] for item in row["activity"] if item}
        done = [f"{phrase}: {counts[how]}" for how, phrase in ROSTER_PHRASES.items() if counts.get(how)]
        if row["received"]:
            done.append(f"mails received: {row['received']}")
        details = ", ".join(row["emails"] or []) or "no e-mail known"
        if row["community"]:
            details += f", community {row['community']}"
        line = f"{row['name']} ({details}): {', '.join(done) or 'no recorded activity'}"
        if row["mail_subjects"]:
            line += ", mail subjects: " + " | ".join(f'"{subject}"' for subject in row["mail_subjects"])
        if row["actor_type"] == "mailbox":
            apart.append(f"{line} - a shared mailbox, not a person")
        elif row["ambiguous"]:
            apart.append(f"{line} - a name-only mention that could be one of several persons")
        else:
            people.append(line)
    more = len(people) - MAX_ROSTER
    text = (f"All persons in the graph ({len(people)} people): " + "; ".join(people[:MAX_ROSTER])
            + (f"; ... and {more} more" if more > 0 else ""))
    if apart:
        text += ". Not counted as people: " + "; ".join(apart)
    return text


MAX_BASIS_EXPERTS = 2  # experts per subject whose basis is given, best rank first
MAX_BASIS_ACTIVITIES = 6  # activities listed per expert, then "... and N more"


def expertise_basis(session, timeout: float, subject_ids: list[str] | None = None,
                    max_rank: int = MAX_BASIS_EXPERTS) -> list[str]:
    """One fact per expert: what their expertise on a subject rests on, e.g. "Erik Nilsson on Mobile session refresh
    endpoint (62.5 % of the recorded activity): wrote pull request backend-api#47; took part in event "..."; ..."."""
    rows = read(session, EXPERTISE_BASIS,
                {"ids": subject_ids, "max_rank": max_rank, "types": list(ACTIVITY_WEIGHTS)}, timeout)
    facts = []
    for row in rows:
        seen, activities = set(), []
        # The weightiest kinds first, as the Expertise layer weighs them (pull requests before messages), so a cut
        # list keeps what counts most; within one kind, in time order (the query's order).
        counted = sorted(
            (a for a in row["activities"] if a["how"] and a["name"]),
            key=lambda a: -ACTIVITY_WEIGHTS.get(a["how"], ("", 0))[1],
        )
        for activity in counted:
            if (activity["how"], activity["name"]) in seen:
                continue
            seen.add((activity["how"], activity["name"]))
            name = f'"{activity["name"]}"' if activity["how"] == "ACTED_IN_EVENT" else activity["name"]
            activities.append(f'{ACTIVITY_PHRASES.get(activity["how"], activity["how"].lower())} {name}')
        if not activities:
            continue
        more = len(activities) - MAX_BASIS_ACTIVITIES
        listed = "; ".join(activities[:MAX_BASIS_ACTIVITIES]) + (f"; ... and {more} more" if more > 0 else "")
        share = f"{round((row['share'] or 0) * 100, 1):g} %"
        facts.append(f"What {row['person']}'s knowledge of {row['subject']} rests on (rank {row['rank']}, "
                     f"{share} of the recorded activity): {listed}")
    return facts


def _collect(session, queries: list[tuple[int, str]], params: dict, timeout: float) -> dict[str, int]:
    """Candidate node id -> best (lowest) priority."""
    priorities: dict[str, int] = {}
    for priority, query in queries:
        for row in read(session, query, params, timeout):
            if row["id"] not in priorities or priority < priorities[row["id"]]:
                priorities[row["id"]] = priority
    return priorities


def _packet(session, name: str, queries: list, entry_ids: list[str], settings: dict, question_vector: list[float],
            extra_params: dict | None = None, reserved_ids: list[str] | None = None) -> dict:
    """`reserved_ids` are always kept, within the same node limit: they take the places of the lowest-ranked nodes."""
    limits = settings["evidence"]
    timeout = settings["query_timeout_seconds"]
    priorities = _collect(session, queries, {"ids": entry_ids, **(extra_params or {})}, timeout)
    reserved = [i for i in (reserved_ids or []) if i not in priorities][: limits["max_nodes_per_specialist"]]
    for node_id in reserved:
        priorities[node_id] = 2
    rows = fetch_nodes(session, list(priorities), timeout)
    similarity = similarities(session, list(priorities), question_vector, timeout)

    # Best priority first; among equal priority the most similar to the question (nodes without an embedding after
    # those with one), then by time. The kept nodes are then listed in time order.
    ranked = sorted((r for r in rows.values() if r["id"] not in reserved), key=lambda r: (
        priorities[r["id"]], r["id"] not in similarity, -similarity.get(r["id"], 0.0), r.get("at") or "9999", r["name"] or "",
    ))
    kept = [rows[i] for i in reserved if i in rows]
    kept += ranked[: limits["max_nodes_per_specialist"] - len(kept)]
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


def _add_participation(session, packet: dict, entry_ids: list[str], settings: dict) -> None:
    """Adds who took part in an entry meeting or mail. Used by both `sources` and `people`, so the answer does not
    depend on which of the two the planner picks."""
    for row in read(session, PARTICIPATION, {"ids": entry_ids}, settings["query_timeout_seconds"]):
        packet["nodes"].append({**row, "priority": 1})


# The sources a topic was built from (its Knowledge-layer bundle), by kind. Versions, comments, reviews, code changes
# and transcript segments are left out: they belong to an issue, document, pull request or meeting that is listed.
TOPIC_SOURCES = """
MATCH (t:Topic) WHERE elementId(t) IN $ids
MATCH (t)-[:DERIVED_FROM]->(s)
WHERE s:Issue OR s:PullRequest OR s:Document OR s:MailMessage OR s:SlackMessage OR s:TeamsMeeting
WITH t, s, [l IN labels(s) WHERE l <> 'Searchable'][0] AS label,
     CASE WHEN s:Document THEN s.document_id + ' (' + split(s.title, ':')[0] + ')'
          WHEN s:TeamsMeeting THEN s.meeting_id + ' (' + s.title + ')'
          ELSE coalesce(s.display_name, s.name) END AS name
ORDER BY name
RETURN t.name AS topic, label, collect(DISTINCT name) AS names
"""
TOPIC_SOURCE_KINDS = [
    ("Issue", "issues"), ("PullRequest", "pull requests"), ("Document", "documents"), ("MailMessage", "mails"),
    ("SlackMessage", "Slack messages"), ("TeamsMeeting", "meetings"),
]

# Mails and Slack messages that name nothing, so no topic was built from them, but that are close to the topic in
# meaning (the topic's embedding) and were sent within its period (first to last event, one day either side): an alert
# or a customer's first mail written before the issue existed. The threshold was measured on the Kvitta data on
# 2026-09-29: from 0.78 up only messages about the topic's own story, below it messages about other stories.
UNLINKED_NEAR_TOPIC = """
MATCH (t:Topic) WHERE elementId(t) IN $ids AND t.embedding IS NOT NULL
MATCH (t)<-[:EVENT_OF_TOPIC]-(e:Event)
WITH t, min(datetime(e.occurred_at)) - duration('P1D') AS first, max(datetime(e.occurred_at)) + duration('P1D') AS last
MATCH (s:Searchable) WHERE (s:MailMessage OR s:SlackMessage) AND NOT (:Topic)-[:DERIVED_FROM]->(s)
  AND datetime(s.sent_at) >= first AND datetime(s.sent_at) <= last
WITH t, s, vector.similarity.cosine(t.embedding, s.embedding) AS similarity
WHERE similarity >= $threshold
WITH t, s ORDER BY similarity DESC
RETURN t.name AS topic,
       collect(DISTINCT s.display_name + ' (' + coalesce(s.sender_name, s.author_name, 'unknown') + ': "'
               + CASE WHEN s:MailMessage THEN s.subject
                      WHEN size(s.body) > $snippet THEN left(s.body, $snippet) + '...' ELSE s.body END
               + '")')[..$limit] AS names
"""
UNLINKED_SNIPPET_CHARS = 160
UNLINKED_SIMILARITY = 0.78
MAX_UNLINKED = 5


def topic_source_facts(session, entry_ids: list[str], timeout: float) -> list[str]:
    """One line per entry topic naming every source it was built from, by kind, and the unlinked messages close to it:
    a complete list for "which sources describe ..." questions, at the cost of one line instead of one evidence node
    per source."""
    by_topic: dict[str, dict[str, list[str]]] = {}
    for row in read(session, TOPIC_SOURCES, {"ids": entry_ids}, timeout):
        by_topic.setdefault(row["topic"], {})[row["label"]] = row["names"]
    near = {row["topic"]: row["names"] for row in read(
        session, UNLINKED_NEAR_TOPIC,
        {"ids": entry_ids, "threshold": UNLINKED_SIMILARITY, "limit": MAX_UNLINKED, "snippet": UNLINKED_SNIPPET_CHARS},
        timeout)}
    facts = []
    for topic, kinds in by_topic.items():
        parts = [f"{title}: {', '.join(kinds[label])}" for label, title in TOPIC_SOURCE_KINDS if kinds.get(label)]
        fact = f'The sources the topic "{topic}" was built from, by kind: ' + "; ".join(parts)
        # An earlier version of a listed message (a Slack edit that added the issue key) is not new.
        listed = {name for names in kinds.values() for name in names}
        unlinked = [name for name in near.get(topic, []) if name.split(" (")[0] not in listed]
        if unlinked:
            fact += (". Also describing this story, found by meaning and time (they name no issue, pull request or "
                     "document, so no link was drawn): " + ", ".join(unlinked))
        facts.append(fact)
    return facts


# The reviews and code changes of every pull request an entry source names, and for a reply the review it answers.
# Only pull requests: issue keys are named in almost every message, so following them mostly brought in unrelated
# versions (measured on the Kvitta test questions on 2026-09-29), while a named pull request is a specific reference.
REFERENCED_PARTS = """
MATCH (s) WHERE elementId(s) IN $ids AND any(l IN labels(s) WHERE l IN $labels)
MATCH (s)-[r:MENTIONS_PULL_REQUEST]->(p:PullRequest)
WHERE r.extracted_by = 'reference-extraction-v1'
MATCH (p)-[:HAS_PR_REVIEW|HAS_CODE_CHANGE]->(c:Searchable)
OPTIONAL MATCH (c)-[:REPLY_TO_PR_REVIEW]->(original:Searchable)
RETURN DISTINCT elementId(p) AS ref, elementId(c) AS id, elementId(original) AS original
"""
MAX_REFERENCE_PLACES = 1
# A referenced part takes a place only if it is this similar to the question. Measured on 2026-09-29: the warning in
# review-008 (reached through its reply) scored 0.651, unrelated parts 0.586 to 0.617.
MIN_REFERENCE_SIMILARITY = 0.64


def referenced_parts(session, entry_ids: list[str], question_vector: list[float], timeout: float) -> list[str]:
    """When an entry source names a pull request ("as I wrote in my review of kvitta-mobile#6"), the review or code
    change of it that best matches the question, so the record referred to is in the evidence and not only the
    message about it. A reply is replaced by the review it answers: that is where the statement was made, and the
    reply quotes it. At most `MAX_REFERENCE_PLACES`, only above `MIN_REFERENCE_SIMILARITY`, entry points excluded."""
    rows = read(session, REFERENCED_PARTS, {"ids": entry_ids, "labels": SOURCE_LABELS}, timeout)
    similarity = similarities(session, list({row["id"] for row in rows}), question_vector, timeout)
    best: dict[str, dict] = {}
    for row in rows:
        score = similarity.get(row["id"])
        if score is None or score < MIN_REFERENCE_SIMILARITY:
            continue
        if row["ref"] not in best or score > best[row["ref"]]["score"]:
            best[row["ref"]] = {"id": row["original"] or row["id"], "score": score}
    chosen = []
    for part in sorted(best.values(), key=lambda p: -p["score"]):
        if part["id"] not in entry_ids and part["id"] not in chosen:
            chosen.append(part["id"])
    return chosen[:MAX_REFERENCE_PLACES]


# The topic most entry points were built into (at least two), for when no topic is itself an entry point.
MAIN_TOPIC_OF_ENTRIES = """
MATCH (t:Topic)-[:DERIVED_FROM]->(n) WHERE elementId(n) IN $ids
WITH t, count(DISTINCT n) AS entries WHERE entries >= 2
RETURN elementId(t) AS id ORDER BY entries DESC, t.slug LIMIT 1
"""


def sources(session, entry_ids, plan, settings, question_vector):
    timeout = settings["query_timeout_seconds"]
    reserved = referenced_parts(session, entry_ids, question_vector, timeout)
    packet = _packet(session, "sources", SOURCES_QUERIES, entry_ids, settings, question_vector, {"labels": SOURCE_LABELS},
                     reserved)
    _add_participation(session, packet, entry_ids, settings)
    # The sources of the story: of the entry topics, or else of the topic most entry points belong to, so the list does
    # not depend on whether search happened to return the topic node itself.
    topic_ids = [row["id"] for row in read(session, "MATCH (t:Topic) WHERE elementId(t) IN $ids RETURN elementId(t) AS id",
                                           {"ids": entry_ids}, timeout)]
    if not topic_ids:
        topic_ids = [row["id"] for row in read(session, MAIN_TOPIC_OF_ENTRIES, {"ids": entry_ids}, timeout)]
    packet["facts"].extend(topic_source_facts(session, topic_ids, timeout))
    return packet


# Every causal path of up to three steps that ends in one of the given events, oldest step first. Causal links run
# within a topic (CAUSED) and across topics (CROSS_TOPIC_CAUSED).
CAUSAL_PATHS_TO = """
MATCH (e:Event) WHERE elementId(e) IN $ids
MATCH path = (:Event)-[:CAUSED|CROSS_TOPIC_CAUSED*1..3]->(e)
RETURN elementId(e) AS id, [n IN nodes(path) | {id: elementId(n), name: n.name, at: n.occurred_at}] AS steps
"""
MAX_CHAIN_FACTS = 2


def causal_chain_facts(session, event_ids: list[str], timeout: float, question_vector: list[float]) -> list[str]:
    """What led to each event in the packet, as the chains of causal links in the graph, oldest step first: one line
    per event instead of several separate event texts, so the answer can follow a cause back across weeks and topics.
    Only for events that no other listed event led to (the ends of the chains), at most `MAX_CHAIN_FACTS`, the events
    most similar to the question first, so a chain from another story that shares a name with the question does not
    push the relevant chain aside."""
    paths: dict[str, list[list[dict]]] = {}
    for row in read(session, CAUSAL_PATHS_TO, {"ids": event_ids}, timeout):
        paths.setdefault(row["id"], []).append(row["steps"])
    earlier = {step["id"] for chains in paths.values() for chain in chains for step in chain[:-1]}
    ends = [event_id for event_id in paths if event_id not in earlier]

    similarity = similarities(session, ends, question_vector, timeout)
    facts = []
    for event_id in sorted(ends, key=lambda i: -similarity.get(i, 0.0))[:MAX_CHAIN_FACTS]:
        chains = paths[event_id]
        # Keep only the longest chains: a chain that is the tail of a longer one says nothing new.
        kept = [c for c in chains if not any(len(o) > len(c) and o[-len(c):] == c for o in chains)]
        target = chains[0][-1]
        lines = [" -> ".join(f'"{s["name"]}" ({str(s["at"])[:10]})' for s in chain[:-1]) for chain in kept]
        facts.append(f'What led to "{target["name"]}" ({str(target["at"])[:10]}), from the causal links in the graph, '
                     f"oldest step first: " + "; and ".join(f"{line} -> this event" for line in sorted(lines)))
    return facts


def causes(session, entry_ids, plan, settings, question_vector):
    packet = _packet(session, "causes", CAUSES_QUERIES, entry_ids, settings, question_vector)
    event_ids = [node["id"] for node in packet["nodes"] if node["label"] == "Event"]
    packet["facts"].extend(causal_chain_facts(session, event_ids, settings["query_timeout_seconds"], question_vector))
    return packet


def architecture(session, entry_ids, plan, settings, question_vector):
    return _packet(session, "architecture", ARCHITECTURE_QUERIES, entry_ids, settings, question_vector)


# Persons outside the team, such as a customer: eligible persons whose e-mail domain differs from the domain most
# persons share, with every mail they sent. Their mail subjects are usually what they reported or asked for.
OUTSIDE_PERSONS = """
MATCH (p:Person:Searchable) WHERE p.email IS NOT NULL
WITH collect(p) AS persons, [p IN collect(p) | split(p.email, '@')[1]] AS domains
WITH persons, reduce(best = null, d IN domains |
     CASE WHEN best IS NULL OR size([x IN domains WHERE x = d]) > size([x IN domains WHERE x = best]) THEN d ELSE best END)
     AS team_domain
UNWIND persons AS p
WITH p, team_domain WHERE split(p.email, '@')[1] <> team_domain
OPTIONAL MATCH (p)-[:SENT_MAIL]->(m:MailMessage)
WITH p, team_domain, m ORDER BY m.sent_at
RETURN p.name AS name, p.email AS email, team_domain,
       collect(CASE WHEN m IS NULL THEN null ELSE {id: m.message_id, subject: m.subject, at: m.sent_at} END) AS mails
"""
MAX_OUTSIDE_MAILS = 8

# Every community (the graph-algorithms layer's groups of people who work together) with its members.
COMMUNITIES = """
MATCH (p:Person)-[:MEMBER_OF_COMMUNITY]->(c:Community)
WITH c, p ORDER BY p.name
RETURN c.community_id AS community, collect(p.name) AS members
ORDER BY community
"""


def community_facts(session, timeout: float) -> list[str]:
    """One line listing every group and its members, so a question about groups, teams or how the people are divided
    is answered from the layer that computed them, however the planner classifies it."""
    rows = read(session, COMMUNITIES, None, timeout)
    if not rows:
        return []
    groups = "; ".join(f"community {row['community']}: {', '.join(row['members'])}" for row in rows)
    return [f"Groups of people who work more with each other than with the rest (Louvain community detection on who "
            f"works with whom), {len(rows)} in total: {groups}"]


def outside_person_facts(session, timeout: float) -> list[str]:
    """One line per person outside the team with the mails they sent, "Re:"/"Fwd:" replies folded into their thread:
    what a customer reported, even when search found only one of their mails."""
    facts = []
    for row in read(session, OUTSIDE_PERSONS, None, timeout):
        threads: dict[str, list[str]] = {}
        for mail in row["mails"]:
            subject = mail["subject"] or ""
            while subject[:4].lower() in ("re: ", "fw: ") or subject[:5].lower() == "fwd: ":
                subject = subject.split(":", 1)[1].strip()
            threads.setdefault(subject, []).append(f'{mail["id"]} {str(mail["at"])[:10]}')
        listed = [f'"{subject}" ({", ".join(mails)})' for subject, mails in list(threads.items())[:MAX_OUTSIDE_MAILS]]
        facts.append(f"{row['name']} ({row['email']}) is outside the team (the team's e-mail domain is "
                     f"{row['team_domain']}). Mails they sent, by subject: " + ("; ".join(listed) or "none"))
    return facts


def people(session, entry_ids, plan, settings, question_vector):
    packet = _packet(session, "people", PEOPLE_QUERIES, entry_ids, settings, question_vector,
                     {"activity": PERSON_ACTIVITY_TYPES})
    _add_participation(session, packet, entry_ids, settings)
    timeout = settings["query_timeout_seconds"]
    packet["facts"].extend(outside_person_facts(session, timeout))
    packet["facts"].extend(community_facts(session, timeout))
    # A general "who" question (no person, meeting or other entity named) gets the full list of persons, so the answer
    # does not depend on which persons search happened to find.
    if "who" in plan.get("question_types", []) and not plan.get("entities"):
        packet["facts"].append(person_roster(session, timeout))
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
        # What the leading experts' knowledge rests on, so the answer can say it in concrete terms.
        packet["facts"].extend(expertise_basis(session, timeout))
    return packet


SPECIALIST_FUNCTIONS = {"sources": sources, "causes": causes, "architecture": architecture, "people": people}


def _use_matched_chunks(session, packet: dict, chunk_ids_by_node: dict[str, list[str]], settings: dict) -> None:
    """A node the search reached through chunks of its long text shows the model those chunks, in text order, instead
    of the beginning of the text: the answer is in the part that matched. Each chunk is at most one chunk window long
    (see `embedding_pass.split_text`), so the text is not cut to `max_text_chars` like other nodes."""
    wanted = [chunk_id for node in packet["nodes"] for chunk_id in chunk_ids_by_node.get(node["id"], [])]
    chunks = chunk_texts(session, wanted, settings["query_timeout_seconds"])
    for node in packet["nodes"]:
        matched = [chunks[chunk_id] for chunk_id in chunk_ids_by_node.get(node["id"], []) if chunk_id in chunks]
        if matched:
            node["text"] = "\n\n".join(chunk["text"] for chunk in sorted(matched, key=lambda c: c["chunk_index"]))


def run_specialist(name: str, driver, database: str, entry_ids: list[str], plan: dict, settings: dict,
                   followup=None, question_vector: list[float] | None = None,
                   chunk_ids_by_node: dict[str, list[str]] | None = None) -> tuple[dict, list[dict]]:
    """Runs one specialist in its own read session (specialists run in parallel; a session is not thread-safe).

    `followup(session, packet) -> usage entries`, when given, extends the packet in the same session.
    `chunk_ids_by_node` maps an entry node to the chunks of its text the search matched. Returns (packet, usage). The
    session is closed when the `with` block ends, also on an error.
    """
    started = time.perf_counter()
    usage: list[dict] = []
    try:
        with driver.session(database=database, default_access_mode="READ") as session:
            packet = SPECIALIST_FUNCTIONS[name](session, entry_ids, plan, settings, question_vector or [])
            packet["errors"] = []
            if chunk_ids_by_node:
                _use_matched_chunks(session, packet, chunk_ids_by_node, settings)
            if followup is not None:
                try:
                    usage = followup(session, packet)
                except Exception as error:  # noqa: BLE001 - a failed follow-up only means no extra evidence
                    packet["followup"] = [f"follow-up failed: {error}"]
    except Exception as error:  # noqa: BLE001 - reported in the packet and in state, never silently dropped
        packet = {"specialist": name, "nodes": [], "facts": [], "candidates": 0, "errors": [str(error)]}
    packet["ms"] = round((time.perf_counter() - started) * 1000)
    return packet, usage
