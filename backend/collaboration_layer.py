"""Collaboration layer: who knows what, and who works with whom.

Fully deterministic. No model is called. Expertise is computed from weighted
activity counted per person per subject (a `Topic` or a `Component`);
collaboration is computed from shared work items between eligible persons.

This module reads and writes Neo4j only. It never touches PostgreSQL.
"""

from reference_extraction import (
    PIPELINE_STATE_ID,
    PIPELINE_STATE_LABEL,
    now_iso,
    touch_pipeline_state,
)

COLLABORATION_VERSION = "collaboration-layer-v1"
MIN_EXPERTISE_SCORE = 2
MAX_SHARED_WORK_ITEMS = 50

# relationship type -> (target label, weight)
ACTIVITY_WEIGHTS = {
    "AUTHORED_PR": ("PullRequest", 3),
    "WROTE_PR_REVIEW": ("PullRequestReview", 2),
    "AUTHORED_DOCUMENT_VERSION": ("DocumentVersion", 2),
    "ACTED_IN_EVENT": ("Event", 2),
    "CHANGED_ISSUE_VERSION": ("IssueVersion", 1),
    "WROTE_ISSUE_COMMENT": ("IssueComment", 1),
    "SENT_SLACK_MESSAGE": ("SlackMessage", 1),
    "SENT_MAIL": ("MailMessage", 1),
    "SPOKE_TEAMS_TRANSCRIPT_SEGMENT": ("TeamsTranscriptSegment", 1),
}

TIMESTAMP_PROPS = ["occurred_at", "version_at", "sent_at", "created_at", "started_at"]


class PrerequisiteError(RuntimeError):
    pass


def to_iso_string(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Eligible / excluded persons.
# ---------------------------------------------------------------------------

def load_eligible_persons(tx):
    return [dict(row) for row in tx.run("""
        MATCH (p:Person)
        WHERE coalesce(p.actor_type, '') <> 'mailbox' AND coalesce(p.identity_ambiguous, false) <> true
        RETURN p.person_key AS person_key, p.name AS name
    """)]


def load_excluded_persons(tx):
    return [dict(row) for row in tx.run("""
        MATCH (p:Person)
        WHERE p.actor_type = 'mailbox' OR p.identity_ambiguous = true
        RETURN p.name AS person_name, p.person_key AS person_key,
               CASE WHEN p.actor_type = 'mailbox' THEN 'mailbox' ELSE 'ambiguous identity' END AS reason
        ORDER BY p.person_key
    """)]


def load_person_names(tx):
    return {row["person_key"]: row["name"] for row in tx.run("MATCH (p:Person) RETURN p.person_key AS person_key, p.name AS name")}


def load_topic_names(tx):
    return {row["slug"]: row["name"] for row in tx.run("MATCH (t:Topic) RETURN t.slug AS slug, t.name AS name")}


def load_component_names(tx):
    return {
        (row["source_instance"], row["repository"], row["slug"]): row["name"]
        for row in tx.run("""
            MATCH (c:Component)
            RETURN c.source_instance AS source_instance, c.repository AS repository, c.slug AS slug, c.name AS name
        """)
    }


# ---------------------------------------------------------------------------
# Activities and subjects.
# ---------------------------------------------------------------------------

def load_activities_for_relationship(tx, relationship_type, target_label, eligible_keys):
    if not eligible_keys:
        return []
    query = f"""
        MATCH (p:Person)-[:{relationship_type}]->(t:{target_label})
        WHERE p.person_key IN $keys
        RETURN p.person_key AS person_key, elementId(t) AS node_id, properties(t) AS props
    """
    return [dict(row) for row in tx.run(query, {"keys": eligible_keys})]


def load_segment_meeting_started_at(tx):
    return {
        row["id"]: row["started_at"]
        for row in tx.run("""
            MATCH (m:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(s:TeamsTranscriptSegment)
            RETURN elementId(s) AS id, m.started_at AS started_at
        """)
    }


def build_subject_map(tx, node_ids):
    subject_map = {nid: {"topics": set(), "components": set()} for nid in node_ids}
    if not node_ids:
        return subject_map

    for row in tx.run("""
        MATCH (t:Topic)-[:DERIVED_FROM]->(n) WHERE elementId(n) IN $ids
        RETURN elementId(n) AS id, t.slug AS slug
    """, {"ids": node_ids}):
        subject_map[row["id"]]["topics"].add(row["slug"])

    for row in tx.run("""
        MATCH (n:Event)-[:EVENT_OF_TOPIC]->(t:Topic) WHERE elementId(n) IN $ids
        RETURN elementId(n) AS id, t.slug AS slug
    """, {"ids": node_ids}):
        subject_map[row["id"]]["topics"].add(row["slug"])

    for row in tx.run("""
        MATCH (n:PullRequest)-[:HAS_CODE_CHANGE]->(:CodeChange)-[:MODIFIES_FILE]->(f:File)<-[:IMPLEMENTED_IN]-(c:Component)
        WHERE elementId(n) IN $ids
        RETURN DISTINCT elementId(n) AS id, c.source_instance AS si, c.repository AS repo, c.slug AS slug
    """, {"ids": node_ids}):
        subject_map[row["id"]]["components"].add((row["si"], row["repo"], row["slug"]))

    for row in tx.run("""
        MATCH (pr:PullRequest)-[:HAS_PR_REVIEW]->(n:PullRequestReview)
        WHERE elementId(n) IN $ids
        MATCH (pr)-[:HAS_CODE_CHANGE]->(:CodeChange)-[:MODIFIES_FILE]->(f:File)<-[:IMPLEMENTED_IN]-(c:Component)
        RETURN DISTINCT elementId(n) AS id, c.source_instance AS si, c.repository AS repo, c.slug AS slug
    """, {"ids": node_ids}):
        subject_map[row["id"]]["components"].add((row["si"], row["repo"], row["slug"]))

    for row in tx.run("""
        MATCH (c:Component)-[:COMPONENT_EVIDENCED_BY]->(n) WHERE elementId(n) IN $ids
        RETURN elementId(n) AS id, c.source_instance AS si, c.repository AS repo, c.slug AS slug
    """, {"ids": node_ids}):
        subject_map[row["id"]]["components"].add((row["si"], row["repo"], row["slug"]))

    for row in tx.run("""
        MATCH (n:Event)-[:AFFECTED_COMPONENT]->(c:Component) WHERE elementId(n) IN $ids
        RETURN elementId(n) AS id, c.source_instance AS si, c.repository AS repo, c.slug AS slug
    """, {"ids": node_ids}):
        subject_map[row["id"]]["components"].add((row["si"], row["repo"], row["slug"]))

    return subject_map


def compute_expertise_aggregation(activities, subject_map):
    agg = {}
    for activity in activities:
        subjects = subject_map.get(activity["node_id"], {"topics": set(), "components": set()})
        subject_keys = [("Topic", slug) for slug in subjects["topics"]]
        subject_keys += [("Component", component_key) for component_key in subjects["components"]]

        for subject_label, subject_key in subject_keys:
            key = (activity["person_key"], subject_label, subject_key)
            entry = agg.setdefault(key, {"score": 0, "node_ids": set(), "timestamps": []})
            if activity["node_id"] not in entry["node_ids"]:
                entry["node_ids"].add(activity["node_id"])
                entry["score"] += activity["weight"]
                if activity["timestamp"]:
                    entry["timestamps"].append(activity["timestamp"])
    return agg


def finalize_expertise(agg):
    by_subject = {}
    for (person_key, subject_label, subject_key), entry in agg.items():
        if entry["score"] < MIN_EXPERTISE_SCORE:
            continue
        by_subject.setdefault((subject_label, subject_key), []).append((person_key, entry))

    results = []
    for (subject_label, subject_key), entries in by_subject.items():
        total_score = sum(entry["score"] for _, entry in entries)
        entries_sorted = sorted(entries, key=lambda pair: (-pair[1]["score"], pair[0]))
        for rank, (person_key, entry) in enumerate(entries_sorted, start=1):
            timestamps = sorted(entry["timestamps"])
            results.append({
                "person_key": person_key,
                "subject_label": subject_label,
                "subject_key": subject_key,
                "score": entry["score"],
                "share": round(entry["score"] / total_score, 3) if total_score else 0.0,
                "rank": rank,
                "activity_count": len(entry["node_ids"]),
                "first_activity_at": timestamps[0] if timestamps else None,
                "last_activity_at": timestamps[-1] if timestamps else None,
                "node_ids": entry["node_ids"],
            })
    return results


# ---------------------------------------------------------------------------
# Work items and collaboration pairs.
# ---------------------------------------------------------------------------

def load_issue_work_items(tx, eligible_keys):
    rows = tx.run("""
        MATCH (i:Issue)
        OPTIONAL MATCH (p1:Person)-[:CREATED_ISSUE]->(i) WHERE p1.person_key IN $keys
        OPTIONAL MATCH (p2:Person)-[:OWNS_ISSUE]->(i) WHERE p2.person_key IN $keys
        OPTIONAL MATCH (p3:Person)-[:COMMENTED_ON_ISSUE]->(i) WHERE p3.person_key IN $keys
        OPTIONAL MATCH (i)-[:HAS_ISSUE_VERSION]->(:IssueVersion)<-[:CHANGED_ISSUE_VERSION]-(p4:Person) WHERE p4.person_key IN $keys
        WITH i, collect(DISTINCT p1.person_key) + collect(DISTINCT p2.person_key)
              + collect(DISTINCT p3.person_key) + collect(DISTINCT p4.person_key) AS keys_raw
        RETURN coalesce(i.display_name, i.issue_key) AS display_name, [k IN keys_raw WHERE k IS NOT NULL] AS person_keys
    """, {"keys": eligible_keys})
    return [{"label": "Issue", "display_name": row["display_name"], "person_keys": set(row["person_keys"])}
            for row in rows if row["person_keys"]]


def load_pr_work_items(tx, eligible_keys):
    rows = tx.run("""
        MATCH (pr:PullRequest)
        OPTIONAL MATCH (p1:Person)-[:AUTHORED_PR]->(pr) WHERE p1.person_key IN $keys
        OPTIONAL MATCH (p2:Person)-[:REVIEWED_PR]->(pr) WHERE p2.person_key IN $keys
        WITH pr, collect(DISTINCT p1.person_key) + collect(DISTINCT p2.person_key) AS keys_raw
        RETURN pr.display_name AS display_name, [k IN keys_raw WHERE k IS NOT NULL] AS person_keys
    """, {"keys": eligible_keys})
    return [{"label": "PullRequest", "display_name": row["display_name"], "person_keys": set(row["person_keys"])}
            for row in rows if row["person_keys"]]


def load_meeting_work_items(tx, eligible_keys):
    rows = tx.run("""
        MATCH (m:TeamsMeeting)
        OPTIONAL MATCH (p:Person)-[:PARTICIPATED_IN_MEETING]->(m) WHERE p.person_key IN $keys
        WITH m, collect(DISTINCT p.person_key) AS keys_raw
        RETURN coalesce(m.display_name, m.title) AS display_name, [k IN keys_raw WHERE k IS NOT NULL] AS person_keys
    """, {"keys": eligible_keys})
    return [{"label": "TeamsMeeting", "display_name": row["display_name"], "person_keys": set(row["person_keys"])}
            for row in rows if row["person_keys"]]


def load_mail_work_items(tx, eligible_keys):
    rows = tx.run("""
        MATCH (mail:MailMessage)
        OPTIONAL MATCH (p1:Person)-[:SENT_MAIL]->(mail) WHERE p1.person_key IN $keys
        OPTIONAL MATCH (mail)-[:MAIL_RECIPIENT]->(p2:Person) WHERE p2.person_key IN $keys
        WITH mail, collect(DISTINCT p1.person_key) + collect(DISTINCT p2.person_key) AS keys_raw
        RETURN coalesce(mail.display_name, mail.subject) AS display_name, [k IN keys_raw WHERE k IS NOT NULL] AS person_keys
    """, {"keys": eligible_keys})
    return [{"label": "MailMessage", "display_name": row["display_name"], "person_keys": set(row["person_keys"])}
            for row in rows if row["person_keys"]]


def load_event_work_items(tx, eligible_keys):
    rows = tx.run("""
        MATCH (e:Event)
        OPTIONAL MATCH (p:Person)-[:ACTED_IN_EVENT]->(e) WHERE p.person_key IN $keys
        WITH e, collect(DISTINCT p.person_key) AS keys_raw
        RETURN coalesce(e.display_name, e.name) AS display_name, [k IN keys_raw WHERE k IS NOT NULL] AS person_keys
    """, {"keys": eligible_keys})
    return [{"label": "Event", "display_name": row["display_name"], "person_keys": set(row["person_keys"])}
            for row in rows if row["person_keys"]]


def compute_works_with(work_items):
    pairs = {}
    for item in work_items:
        keys = sorted(item["person_keys"])
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                pair_key = (keys[i], keys[j])
                entry = pairs.setdefault(pair_key, {"weight": 0, "shared_work_items": [], "work_item_types": set()})
                entry["weight"] += 1
                if len(entry["shared_work_items"]) < MAX_SHARED_WORK_ITEMS:
                    entry["shared_work_items"].append(item["display_name"])
                entry["work_item_types"].add(item["label"])
    return pairs


# ---------------------------------------------------------------------------
# Writes.
# ---------------------------------------------------------------------------

def ensure_collaboration_constraints(tx):
    tx.run("""
        CREATE CONSTRAINT expertise_key IF NOT EXISTS
        FOR (n:Expertise) REQUIRE (n.person_key, n.subject_label, n.subject_key) IS UNIQUE
    """)


def write_expertise(tx, props):
    tx.run("""
        MERGE (e:Expertise {person_key: $person_key, subject_label: $subject_label, subject_key: $subject_key})
        SET e.score = $score, e.share = $share, e.rank = $rank, e.activity_count = $activity_count,
            e.first_activity_at = $first_activity_at, e.last_activity_at = $last_activity_at,
            e.display_name = $display_name, e.derived = true,
            e.generated_by = $version, e.generated_at = $generated_at
    """, props)


def write_has_expertise(tx, person_key, subject_label, subject_key, generated_at):
    tx.run("""
        MATCH (p:Person {person_key: $person_key})
        MATCH (e:Expertise {person_key: $person_key, subject_label: $subject_label, subject_key: $subject_key})
        MERGE (p)-[r:HAS_EXPERTISE]->(e)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"person_key": person_key, "subject_label": subject_label, "subject_key": subject_key,
          "version": COLLABORATION_VERSION, "generated_at": generated_at})


def write_expertise_in_topic(tx, person_key, subject_key, generated_at):
    tx.run("""
        MATCH (e:Expertise {person_key: $person_key, subject_label: 'Topic', subject_key: $subject_key})
        MATCH (t:Topic {slug: $subject_key})
        MERGE (e)-[r:EXPERTISE_IN]->(t)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"person_key": person_key, "subject_key": subject_key,
          "version": COLLABORATION_VERSION, "generated_at": generated_at})


def write_expertise_in_component(tx, person_key, subject_key, source_instance, repository, slug, generated_at):
    tx.run("""
        MATCH (e:Expertise {person_key: $person_key, subject_label: 'Component', subject_key: $subject_key})
        MATCH (c:Component {source_instance: $si, repository: $repo, slug: $slug})
        MERGE (e)-[r:EXPERTISE_IN]->(c)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"person_key": person_key, "subject_key": subject_key, "si": source_instance, "repo": repository,
          "slug": slug, "version": COLLABORATION_VERSION, "generated_at": generated_at})


def write_expertise_evidenced_by(tx, person_key, subject_label, subject_key, node_ids, generated_at):
    if not node_ids:
        return
    tx.run("""
        MATCH (e:Expertise {person_key: $person_key, subject_label: $subject_label, subject_key: $subject_key})
        UNWIND $node_ids AS nid
        MATCH (n) WHERE elementId(n) = nid
        MERGE (e)-[r:EXPERTISE_EVIDENCED_BY]->(n)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"person_key": person_key, "subject_label": subject_label, "subject_key": subject_key,
          "node_ids": node_ids, "version": COLLABORATION_VERSION, "generated_at": generated_at})


def write_works_with(tx, person_a, person_b, weight, shared_work_items, work_item_types, generated_at):
    tx.run("""
        MATCH (a:Person {person_key: $a})
        MATCH (b:Person {person_key: $b})
        MERGE (a)-[r:WORKS_WITH]->(b)
        SET r.weight = $weight, r.shared_work_items = $shared_work_items, r.work_item_types = $work_item_types,
            r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"a": person_a, "b": person_b, "weight": weight, "shared_work_items": shared_work_items,
          "work_item_types": work_item_types, "version": COLLABORATION_VERSION, "generated_at": generated_at})


def delete_generated_relationships(tx):
    result = tx.run("""
        MATCH ()-[r]->() WHERE r.generated_by = $version
        DELETE r
        RETURN count(r) AS deleted
    """, {"version": COLLABORATION_VERSION})
    return result.single()["deleted"]


def delete_generated_nodes(tx):
    result = tx.run("""
        MATCH (n) WHERE n.generated_by = $version
        DETACH DELETE n
        RETURN count(n) AS deleted
    """, {"version": COLLABORATION_VERSION})
    return result.single()["deleted"]


# ---------------------------------------------------------------------------
# State reads.
# ---------------------------------------------------------------------------

def read_pipeline_state(tx):
    row = tx.run(f"""
        MATCH (s:{PIPELINE_STATE_LABEL} {{id: $id}})
        RETURN s.last_import_at AS last_import_at, s.last_layer_build_at AS last_layer_build_at,
               s.last_architecture_build_at AS last_architecture_build_at,
               s.last_causal_build_at AS last_causal_build_at,
               s.last_collaboration_build_at AS last_collaboration_build_at
    """, {"id": PIPELINE_STATE_ID}).single()
    if not row:
        return {"last_import_at": None, "last_layer_build_at": None, "last_architecture_build_at": None,
                "last_causal_build_at": None, "last_collaboration_build_at": None}
    return dict(row)


def needs_collaboration_rerun(state):
    own = state.get("last_collaboration_build_at")
    if not own:
        return True
    for key in ("last_import_at", "last_layer_build_at", "last_architecture_build_at", "last_causal_build_at"):
        upstream = state.get(key)
        if upstream and upstream > own:
            return True
    return False


def load_counts(tx):
    return {
        "expertise": tx.run("MATCH (e:Expertise) RETURN count(e) AS c").single()["c"],
        "persons_with_expertise": tx.run("MATCH (p:Person)-[:HAS_EXPERTISE]->() RETURN count(DISTINCT p) AS c").single()["c"],
        "works_with_pairs": tx.run("MATCH ()-[r:WORKS_WITH]->() RETURN count(r) AS c").single()["c"],
        "excluded_persons": tx.run("""
            MATCH (p:Person) WHERE p.actor_type = 'mailbox' OR p.identity_ambiguous = true
            RETURN count(p) AS c
        """).single()["c"],
    }


def load_expertise_rows(tx):
    return [dict(row) for row in tx.run("""
        MATCH (p:Person)-[:HAS_EXPERTISE]->(e:Expertise)
        OPTIONAL MATCH (e)-[:EXPERTISE_IN]->(subject)
        RETURN p.name AS person_name, e.subject_label AS subject_label,
               coalesce(subject.name, subject.display_name) AS subject_name,
               e.score AS score, e.share AS share, e.rank AS rank, e.activity_count AS activity_count,
               e.first_activity_at AS first_activity_at, e.last_activity_at AS last_activity_at
        ORDER BY subject_name, e.rank
    """)]


def load_works_with_rows(tx):
    return [dict(row) for row in tx.run("""
        MATCH (a:Person)-[r:WORKS_WITH]->(b:Person)
        RETURN a.name AS person_a, b.name AS person_b, r.weight AS weight,
               r.work_item_types AS work_item_types, r.shared_work_items AS shared_work_items
        ORDER BY r.weight DESC
    """)]


def load_collaboration_state(session):
    def _read(tx):
        return {
            "state": read_pipeline_state(tx),
            "counts": load_counts(tx),
            "expertise": load_expertise_rows(tx),
            "works_with": load_works_with_rows(tx),
            "excluded_persons": load_excluded_persons(tx),
        }

    result = session.execute_read(_read)
    state = result["state"]
    return {
        "last_import_at": state["last_import_at"],
        "last_layer_build_at": state["last_layer_build_at"],
        "last_architecture_build_at": state["last_architecture_build_at"],
        "last_causal_build_at": state["last_causal_build_at"],
        "last_collaboration_build_at": state["last_collaboration_build_at"],
        "needs_rerun": needs_collaboration_rerun(state),
        "counts": result["counts"],
        "expertise": result["expertise"],
        "works_with": result["works_with"],
        "excluded_persons": result["excluded_persons"],
    }


def collaboration_state_payload(session):
    return load_collaboration_state(session)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def build_collaboration_layer(session):
    state = session.execute_read(read_pipeline_state)
    if not (state.get("last_layer_build_at") and state.get("last_architecture_build_at") and state.get("last_causal_build_at")):
        raise PrerequisiteError("Run Knowledge, Architecture and Causal layers first.")

    generated_at = now_iso()
    session.execute_write(ensure_collaboration_constraints)

    eligible = session.execute_read(load_eligible_persons)
    eligible_keys = [person["person_key"] for person in eligible]
    person_names = session.execute_read(load_person_names)
    topic_names = session.execute_read(load_topic_names)
    component_names = session.execute_read(load_component_names)

    all_activities = []
    for relationship_type, (target_label, weight) in ACTIVITY_WEIGHTS.items():
        rows = session.execute_read(load_activities_for_relationship, relationship_type, target_label, eligible_keys)
        for row in rows:
            all_activities.append({
                "person_key": row["person_key"], "node_id": row["node_id"],
                "label": target_label, "weight": weight, "props": row["props"],
            })

    segment_meeting_map = session.execute_read(load_segment_meeting_started_at)
    for activity in all_activities:
        if activity["label"] == "TeamsTranscriptSegment":
            activity["timestamp"] = to_iso_string(segment_meeting_map.get(activity["node_id"]))
            continue
        activity["timestamp"] = None
        for prop in TIMESTAMP_PROPS:
            value = activity["props"].get(prop)
            if value:
                activity["timestamp"] = to_iso_string(value)
                break

    node_ids = list({activity["node_id"] for activity in all_activities})
    subject_map = session.execute_read(build_subject_map, node_ids)

    agg = compute_expertise_aggregation(all_activities, subject_map)
    expertise_rows = finalize_expertise(agg)

    work_items = []
    work_items += session.execute_read(load_issue_work_items, eligible_keys)
    work_items += session.execute_read(load_pr_work_items, eligible_keys)
    work_items += session.execute_read(load_meeting_work_items, eligible_keys)
    work_items += session.execute_read(load_mail_work_items, eligible_keys)
    work_items += session.execute_read(load_event_work_items, eligible_keys)

    works_with_pairs = compute_works_with(work_items)

    deleted_relationships = session.execute_write(delete_generated_relationships)
    deleted_nodes = session.execute_write(delete_generated_nodes)

    for row in expertise_rows:
        person_name = person_names.get(row["person_key"], row["person_key"])
        if row["subject_label"] == "Topic":
            subject_key_str = row["subject_key"]
            subject_name = topic_names.get(row["subject_key"], row["subject_key"])
        else:
            source_instance, repository, slug = row["subject_key"]
            subject_key_str = f"{source_instance}/{repository}/{slug}"
            subject_name = component_names.get(row["subject_key"], slug)

        session.execute_write(write_expertise, {
            "person_key": row["person_key"], "subject_label": row["subject_label"], "subject_key": subject_key_str,
            "score": row["score"], "share": row["share"], "rank": row["rank"],
            "activity_count": row["activity_count"], "first_activity_at": row["first_activity_at"],
            "last_activity_at": row["last_activity_at"], "display_name": f"{person_name} – {subject_name}",
            "version": COLLABORATION_VERSION, "generated_at": generated_at,
        })
        session.execute_write(write_has_expertise, row["person_key"], row["subject_label"], subject_key_str, generated_at)
        if row["subject_label"] == "Topic":
            session.execute_write(write_expertise_in_topic, row["person_key"], subject_key_str, generated_at)
        else:
            source_instance, repository, slug = row["subject_key"]
            session.execute_write(write_expertise_in_component, row["person_key"], subject_key_str, source_instance, repository, slug, generated_at)
        session.execute_write(write_expertise_evidenced_by, row["person_key"], row["subject_label"], subject_key_str, list(row["node_ids"]), generated_at)

    for (person_a, person_b), entry in works_with_pairs.items():
        session.execute_write(
            write_works_with, person_a, person_b, entry["weight"],
            entry["shared_work_items"], sorted(entry["work_item_types"]), generated_at,
        )

    session.execute_write(touch_pipeline_state, "last_collaboration_build_at", generated_at)

    report = load_collaboration_state(session)
    report.update({
        "built_at": generated_at,
        "deleted_relationships": deleted_relationships,
        "deleted_nodes": deleted_nodes,
    })
    return report
