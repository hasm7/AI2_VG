"""Causal layer: root causes, code contributions, affected components, and
causal links between events in different topics.

The Knowledge layer already creates `(:Event)-[:CAUSED]->(:Event)` inside one
topic. This layer never creates, changes, or deletes those relationships; it
extends the causal picture around them: why something happened
(`RootCause`), which code changes contributed to which events
(`CONTRIBUTED_TO`), which components an event affected
(`AFFECTED_COMPONENT`), and causal links that cross topic boundaries
(`CROSS_TOPIC_CAUSED`).

One model call per `Topic`. As with the Architecture layer, the old layer is
only replaced after every model call in the run has succeeded.

This module reads and writes Neo4j only. It never touches PostgreSQL.
"""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from reference_extraction import now_iso, touch_pipeline_state
from topic_event_extraction import require_openai_client
from pipeline_staleness import compute_staleness
from pipeline_staleness import read_pipeline_state as _read_full_pipeline_state

OPENAI_MODEL = "gpt-5.6-terra"
OPENAI_REASONING_EFFORT = "medium"
CAUSAL_VERSION = "causal-layer-v1"

CANDIDATE_WINDOW_DAYS = 30
MAX_CANDIDATE_EVENTS = 30
MAX_ROOT_CAUSES = 10
MAX_CODE_CONTRIBUTIONS = 20
MAX_AFFECTED_COMPONENTS = 20
MAX_CROSS_TOPIC_LINKS = 10
TRUNCATE_LENGTH = 4000


class PrerequisiteError(RuntimeError):
    pass


def trunc(value):
    if value is None:
        return None
    return value[:TRUNCATE_LENGTH]


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def source_identifier(label, props):
    if label == "Issue":
        return props.get("issue_key")
    if label == "IssueVersion":
        return props.get("display_name")
    if label == "IssueComment":
        return props.get("comment_id")
    if label == "MailMessage":
        return props.get("message_id")
    if label == "SlackMessage":
        return f'{props.get("message_id")} v{props.get("version_number")}'
    if label == "TeamsMeeting":
        return props.get("meeting_id")
    if label == "TeamsTranscriptSegment":
        return props.get("segment_id")
    if label == "Document":
        return props.get("document_id")
    if label == "DocumentVersion":
        return props.get("display_name")
    if label == "PullRequest":
        return props.get("display_name")
    if label == "PullRequestReview":
        return props.get("source_id")
    return props.get("display_name") or props.get("name")


def source_text(label, props):
    if label in ("MailMessage",):
        return "\n".join(filter(None, [trunc(props.get("subject")), trunc(props.get("body"))]))
    if label in ("SlackMessage", "TeamsTranscriptSegment", "IssueComment", "PullRequestReview"):
        return trunc(props.get("body"))
    if label == "TeamsMeeting":
        return trunc(props.get("title"))
    if label == "Issue":
        return "\n".join(filter(None, [trunc(props.get("title")), trunc(props.get("description"))]))
    if label == "IssueVersion":
        return "\n".join(filter(None, [
            trunc(props.get("title")), trunc(props.get("description")), trunc(props.get("acceptance_criteria")),
        ]))
    if label == "Document":
        return "\n".join(filter(None, [trunc(props.get("title")), trunc(props.get("body"))]))
    if label == "DocumentVersion":
        return "\n".join(filter(None, [trunc(props.get("title")), trunc(props.get("body")), trunc(props.get("change_summary"))]))
    if label == "PullRequest":
        return "\n".join(filter(None, [trunc(props.get("title")), trunc(props.get("description"))]))
    return ""


# ---------------------------------------------------------------------------
# Model output schema.
# ---------------------------------------------------------------------------

class RootCauseOut(BaseModel):
    slug: str
    name: str
    cause_type: Literal[
        "requirement_gap", "design_decision", "implementation_gap", "missing_test", "process", "external", "other"
    ]
    summary: str
    explains_event_ids: list[str]
    component_ids: list[str]
    explanation: str
    evidence: list[str]


class CodeContributionOut(BaseModel):
    code_change_id: str
    event_id: str
    contribution_type: Literal["introduced", "resolved", "partially_resolved", "related"]
    explanation: str
    evidence: list[str]


class AffectedComponentOut(BaseModel):
    event_id: str
    component_id: str
    explanation: str
    evidence: list[str]


class CrossTopicLinkOut(BaseModel):
    cause_event_id: str
    effect_event_id: str
    explanation: str
    evidence: list[str]


class CausalExtractionOut(BaseModel):
    root_causes: list[RootCauseOut]
    code_contributions: list[CodeContributionOut]
    affected_components: list[AffectedComponentOut]
    cross_topic_links: list[CrossTopicLinkOut]


CAUSAL_INSTRUCTIONS = """\
You extend the causal picture around one topic's events: root causes, code \
contributions, affected components, and causal links that cross into other \
topics.

Definitions:
- A root cause is the underlying reason one or more events happened, for \
example a gap in a requirement, a design decision, an implementation that \
only covered part of the system, a missing test, or a process problem. It \
is not the same as the event itself.
- A code contribution means a specific code change contributed to an event, \
for example a change that caused a regression or a change that resolved an \
issue.
- An affected component means the event concerns or impacts that component.
- Cross-topic links are only allowed between one event in this topic and \
one event in the candidate list from other topics. The cause must not \
happen after the effect.

Rules:
- Only claim what the bundle supports. Do not use general knowledge.
- Every item must cite evidence identifiers, copied exactly from the bundle.
- Event and component identifiers you reference must be copied exactly from \
the ids given to you; do not invent new ones.
- The existing causal links between this topic's events are read-only \
context. Do not repropose them.
"""


# ---------------------------------------------------------------------------
# Reads: topics and per-topic bundle assembly.
# ---------------------------------------------------------------------------

def load_topics(tx):
    return [dict(row) for row in tx.run("""
        MATCH (t:Topic)
        RETURN t.slug AS slug, t.name AS name, t.topic_type AS topic_type, t.summary AS summary
        ORDER BY t.slug
    """)]


def load_topic_events(tx, topic_slug):
    return [dict(row) for row in tx.run("""
        MATCH (e:Event {topic_slug: $slug})
        RETURN e.slug AS slug, e.name AS name, e.event_type AS event_type,
               e.occurred_at AS occurred_at, e.summary AS summary
        ORDER BY e.occurred_at
    """, {"slug": topic_slug})]


def load_existing_caused(tx, topic_slug):
    return [dict(row) for row in tx.run("""
        MATCH (cause:Event {topic_slug: $slug})-[r:CAUSED]->(effect:Event {topic_slug: $slug})
        RETURN cause.slug AS cause_slug, cause.name AS cause_name,
               effect.slug AS effect_slug, effect.name AS effect_name, r.explanation AS explanation
    """, {"slug": topic_slug})]


def load_event_evidence(tx, topic_slug):
    return [dict(row) for row in tx.run("""
        MATCH (e:Event {topic_slug: $slug})-[:EVIDENCED_BY]->(n)
        RETURN DISTINCT elementId(n) AS id, labels(n) AS labels, properties(n) AS props
    """, {"slug": topic_slug})]


def load_topic_pull_requests(tx, topic_slug):
    return [dict(row) for row in tx.run("""
        MATCH (t:Topic {slug: $slug})-[:DERIVED_FROM]->(pr:PullRequest)
        RETURN elementId(pr) AS id
    """, {"slug": topic_slug})]


def load_pr_code_changes(tx, pr_ids):
    if not pr_ids:
        return []
    return [dict(row) for row in tx.run("""
        UNWIND $pr_ids AS pid
        MATCH (pr) WHERE elementId(pr) = pid
        MATCH (pr)-[:HAS_CODE_CHANGE]->(c:CodeChange)
        RETURN elementId(c) AS id, c.display_name AS display_name, c.version_number AS version_number,
               c.file_path AS file_path, c.before_summary AS before_summary,
               c.after_summary AS after_summary, c.diff AS diff,
               coalesce(c.source_instance, pr.source_instance) AS source_instance,
               coalesce(c.repository, pr.repository) AS repository
    """, {"pr_ids": pr_ids})]


def load_components_in_repositories(tx, repo_pairs):
    if not repo_pairs:
        return []
    params = [{"source_instance": si, "repository": repo} for si, repo in repo_pairs]
    return [dict(row) for row in tx.run("""
        UNWIND $repos AS repo
        MATCH (c:Component {source_instance: repo.source_instance, repository: repo.repository})
        OPTIONAL MATCH (c)-[:IMPLEMENTED_IN]->(f:File)
        WITH c, collect(DISTINCT f.path) AS files
        RETURN DISTINCT elementId(c) AS id, c.source_instance AS source_instance, c.repository AS repository,
               c.slug AS slug, c.name AS name, c.component_type AS component_type,
               c.summary AS summary, files
    """, {"repos": params})]


def load_all_other_topic_events(tx, topic_slug):
    return [dict(row) for row in tx.run("""
        MATCH (e:Event) WHERE e.topic_slug <> $slug AND e.occurred_at IS NOT NULL
        MATCH (t:Topic {slug: e.topic_slug})
        RETURN e.topic_slug AS topic_slug, t.name AS topic_name, e.slug AS slug, e.name AS name,
               e.event_type AS event_type, e.occurred_at AS occurred_at, e.summary AS summary
    """, {"slug": topic_slug})]


def select_candidate_events(topic_event_times, all_candidates):
    window_seconds = CANDIDATE_WINDOW_DAYS * 86400
    scored = []
    for candidate in all_candidates:
        candidate_dt = parse_dt(candidate["occurred_at"])
        if candidate_dt is None:
            continue
        best_distance = None
        for event_dt in topic_event_times:
            if event_dt is None:
                continue
            distance = abs((candidate_dt - event_dt).total_seconds())
            if distance <= window_seconds and (best_distance is None or distance < best_distance):
                best_distance = distance
        if best_distance is not None:
            scored.append((best_distance, candidate))
    scored.sort(key=lambda pair: pair[0])
    return [candidate for _, candidate in scored[:MAX_CANDIDATE_EVENTS]]


def assemble_causal_bundle(tx, topic):
    slug = topic["slug"]

    events = load_topic_events(tx, slug)
    event_ref_by_id = {}
    event_time_by_id = {}
    topic_event_times = []
    for event in events:
        identifier = f'event:{slug}/{event["slug"]}'
        event_ref_by_id[identifier] = (slug, event["slug"])
        dt = parse_dt(event["occurred_at"])
        event_time_by_id[identifier] = dt
        topic_event_times.append(dt)
    event_ids_in_topic = set(event_ref_by_id.keys())

    existing_caused = load_existing_caused(tx, slug)

    evidence_items = {}
    node_id_by_identifier = {}
    for row in load_event_evidence(tx, slug):
        label = row["labels"][0] if row["labels"] else None
        props = row["props"]
        identifier = source_identifier(label, props)
        if not identifier or identifier in evidence_items:
            continue
        evidence_items[identifier] = {
            "label": label, "display_name": props.get("display_name") or props.get("name") or identifier,
            "text": source_text(label, props),
        }
        node_id_by_identifier[identifier] = row["id"]

    pr_ids = [row["id"] for row in load_topic_pull_requests(tx, slug)]
    code_changes = load_pr_code_changes(tx, pr_ids)
    code_change_items = {}
    repo_pairs = set()
    for change in code_changes:
        identifier = f'{change["display_name"]} v{change["version_number"]}'
        code_change_items[identifier] = change
        node_id_by_identifier[identifier] = change["id"]
        repo_pairs.add((change["source_instance"], change["repository"]))

    components = load_components_in_repositories(tx, sorted(repo_pairs))
    component_items = {}
    for component in components:
        identifier = f'component:{component["repository"]}/{component["slug"]}'
        component_items[identifier] = component

    all_candidates = load_all_other_topic_events(tx, slug)
    candidates = select_candidate_events(topic_event_times, all_candidates)
    candidate_ids = set()
    for candidate in candidates:
        identifier = f'event:{candidate["topic_slug"]}/{candidate["slug"]}'
        candidate_ids.add(identifier)
        event_ref_by_id[identifier] = (candidate["topic_slug"], candidate["slug"])
        event_time_by_id[identifier] = parse_dt(candidate["occurred_at"])

    valid_identifiers = set(evidence_items.keys()) | set(code_change_items.keys())

    return {
        "topic": topic,
        "events": events,
        "event_ids_in_topic": event_ids_in_topic,
        "event_ref_by_id": event_ref_by_id,
        "event_time_by_id": event_time_by_id,
        "existing_caused": existing_caused,
        "evidence_items": evidence_items,
        "code_change_items": code_change_items,
        "component_items": component_items,
        "candidates": candidates,
        "candidate_ids": candidate_ids,
        "valid_identifiers": valid_identifiers,
        "node_id_by_identifier": node_id_by_identifier,
    }


def call_model(client, bundle):
    payload = {
        "topic": {
            "slug": bundle["topic"]["slug"], "name": bundle["topic"]["name"],
            "topic_type": bundle["topic"]["topic_type"], "summary": bundle["topic"]["summary"],
        },
        "events": [
            {"identifier": f'event:{bundle["topic"]["slug"]}/{e["slug"]}', "name": e["name"],
             "event_type": e["event_type"], "occurred_at": e["occurred_at"], "summary": e["summary"]}
            for e in bundle["events"]
        ],
        "existing_causal_links_read_only": bundle["existing_caused"],
        "evidence_bundle": [
            {"identifier": identifier, "label": item["label"], "display_name": item["display_name"], "text": item["text"]}
            for identifier, item in bundle["evidence_items"].items()
        ],
        "code_changes": [
            {"identifier": identifier, "file_path": c.get("file_path"),
             "before_summary": trunc(c.get("before_summary")), "after_summary": trunc(c.get("after_summary")),
             "diff": trunc(c.get("diff"))}
            for identifier, c in bundle["code_change_items"].items()
        ],
        "components": [
            {"identifier": identifier, "name": c.get("name"), "component_type": c.get("component_type"),
             "summary": c.get("summary"), "files": c.get("files")}
            for identifier, c in bundle["component_items"].items()
        ],
        "candidate_events_from_other_topics": [
            {"identifier": f'event:{c["topic_slug"]}/{c["slug"]}', "name": c["name"], "event_type": c["event_type"],
             "occurred_at": c["occurred_at"], "summary": c["summary"], "topic_name": c["topic_name"]}
            for c in bundle["candidates"]
        ],
    }
    return client.responses.parse(
        model=OPENAI_MODEL,
        reasoning={"effort": OPENAI_REASONING_EFFORT},
        instructions=CAUSAL_INSTRUCTIONS,
        input=json.dumps(payload, ensure_ascii=False, default=str),
        text_format=CausalExtractionOut,
    )


def apply_causal_validation(parsed, bundle):
    valid_identifiers = bundle["valid_identifiers"]
    event_ids_in_topic = bundle["event_ids_in_topic"]
    component_ids = set(bundle["component_items"].keys())
    candidate_ids = bundle["candidate_ids"]
    event_time_by_id = bundle["event_time_by_id"]

    discarded_evidence = 0

    def filter_evidence(items):
        nonlocal discarded_evidence
        kept = [item for item in items if item in valid_identifiers]
        discarded_evidence += len(items) - len(kept)
        return kept

    kept_root_causes = []
    for rc in parsed.root_causes:
        kept_evidence = filter_evidence(rc.evidence)
        explains = [eid for eid in rc.explains_event_ids if eid in event_ids_in_topic]
        components = [cid for cid in rc.component_ids if cid in component_ids]
        if not explains or not kept_evidence:
            continue
        kept_root_causes.append({
            "slug": rc.slug, "name": rc.name, "cause_type": rc.cause_type, "summary": rc.summary,
            "explains_event_ids": explains, "component_ids": components,
            "explanation": rc.explanation, "evidence": kept_evidence,
        })
    kept_root_causes = kept_root_causes[:MAX_ROOT_CAUSES]

    kept_contributions = []
    seen_contributions = set()
    for contribution in parsed.code_contributions:
        if contribution.code_change_id not in bundle["code_change_items"]:
            continue
        if contribution.event_id not in event_ids_in_topic:
            continue
        kept_evidence = filter_evidence(contribution.evidence)
        if not kept_evidence:
            continue
        key = (contribution.code_change_id, contribution.event_id)
        if key in seen_contributions:
            continue
        seen_contributions.add(key)
        kept_contributions.append({
            "code_change_id": contribution.code_change_id, "event_id": contribution.event_id,
            "contribution_type": contribution.contribution_type, "explanation": contribution.explanation,
            "evidence": kept_evidence,
        })
    kept_contributions = kept_contributions[:MAX_CODE_CONTRIBUTIONS]

    kept_affected = []
    seen_affected = set()
    for affected in parsed.affected_components:
        if affected.event_id not in event_ids_in_topic:
            continue
        if affected.component_id not in component_ids:
            continue
        kept_evidence = filter_evidence(affected.evidence)
        if not kept_evidence:
            continue
        key = (affected.event_id, affected.component_id)
        if key in seen_affected:
            continue
        seen_affected.add(key)
        kept_affected.append({
            "event_id": affected.event_id, "component_id": affected.component_id,
            "explanation": affected.explanation, "evidence": kept_evidence,
        })
    kept_affected = kept_affected[:MAX_AFFECTED_COMPONENTS]

    kept_cross = []
    for link in parsed.cross_topic_links:
        cause_in_topic = link.cause_event_id in event_ids_in_topic
        effect_in_topic = link.effect_event_id in event_ids_in_topic
        cause_in_candidates = link.cause_event_id in candidate_ids
        effect_in_candidates = link.effect_event_id in candidate_ids
        valid_pair = (cause_in_topic and effect_in_candidates) or (effect_in_topic and cause_in_candidates)
        if not valid_pair:
            continue

        cause_time = event_time_by_id.get(link.cause_event_id)
        effect_time = event_time_by_id.get(link.effect_event_id)
        if cause_time and effect_time and cause_time > effect_time:
            continue

        kept_evidence = filter_evidence(link.evidence)
        if not kept_evidence:
            continue
        kept_cross.append({
            "cause_event_id": link.cause_event_id, "effect_event_id": link.effect_event_id,
            "explanation": link.explanation, "evidence": kept_evidence,
        })
    kept_cross = kept_cross[:MAX_CROSS_TOPIC_LINKS]

    return {
        "root_causes": kept_root_causes, "code_contributions": kept_contributions,
        "affected_components": kept_affected, "cross_topic_links": kept_cross,
        "discarded_evidence": discarded_evidence,
    }


def dedupe_cross_topic_links(all_links):
    seen = set()
    kept = []
    for link in all_links:
        key = (link["cause_event_id"], link["effect_event_id"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(link)
    return kept


# ---------------------------------------------------------------------------
# Writes.
# ---------------------------------------------------------------------------

def ensure_causal_constraints(tx):
    tx.run("""
        CREATE CONSTRAINT root_cause_slug IF NOT EXISTS
        FOR (n:RootCause) REQUIRE n.slug IS UNIQUE
    """)


def write_root_cause(tx, root_cause, generated_at):
    tx.run("""
        MERGE (rc:RootCause {slug: $slug})
        ON CREATE SET rc.name = $name, rc.cause_type = $cause_type, rc.summary = $summary,
                      rc.display_name = $name, rc.derived = true, rc.generated_by = $version,
                      rc.model = $model, rc.generated_at = $generated_at
    """, {"slug": root_cause["slug"], "name": root_cause["name"], "cause_type": root_cause["cause_type"],
          "summary": root_cause["summary"], "version": CAUSAL_VERSION, "model": OPENAI_MODEL,
          "generated_at": generated_at})


def write_has_root_cause(tx, topic_slug, event_slug, root_cause_slug, explanation, evidence, generated_at):
    tx.run("""
        MATCH (e:Event {topic_slug: $topic_slug, slug: $event_slug})
        MATCH (rc:RootCause {slug: $rc_slug})
        MERGE (e)-[r:HAS_ROOT_CAUSE]->(rc)
        SET r.explanation = $explanation, r.evidence = $evidence,
            r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"topic_slug": topic_slug, "event_slug": event_slug, "rc_slug": root_cause_slug,
          "explanation": explanation, "evidence": evidence, "version": CAUSAL_VERSION, "generated_at": generated_at})


def write_root_cause_in_component(tx, root_cause_slug, component_node_id, generated_at):
    tx.run("""
        MATCH (rc:RootCause {slug: $rc_slug})
        MATCH (c) WHERE elementId(c) = $component_node_id
        MERGE (rc)-[r:ROOT_CAUSE_IN_COMPONENT]->(c)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"rc_slug": root_cause_slug, "component_node_id": component_node_id,
          "version": CAUSAL_VERSION, "generated_at": generated_at})


def write_root_cause_evidenced_by(tx, root_cause_slug, node_ids, generated_at):
    if not node_ids:
        return
    tx.run("""
        MATCH (rc:RootCause {slug: $rc_slug})
        UNWIND $node_ids AS nid
        MATCH (n) WHERE elementId(n) = nid
        MERGE (rc)-[r:ROOT_CAUSE_EVIDENCED_BY]->(n)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"rc_slug": root_cause_slug, "node_ids": node_ids, "version": CAUSAL_VERSION, "generated_at": generated_at})


def write_contributed_to(tx, code_change_node_id, topic_slug, event_slug, contribution_type, explanation, evidence, generated_at):
    tx.run("""
        MATCH (c) WHERE elementId(c) = $code_change_node_id
        MATCH (e:Event {topic_slug: $topic_slug, slug: $event_slug})
        MERGE (c)-[r:CONTRIBUTED_TO]->(e)
        SET r.contribution_type = $contribution_type, r.explanation = $explanation, r.evidence = $evidence,
            r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"code_change_node_id": code_change_node_id, "topic_slug": topic_slug, "event_slug": event_slug,
          "contribution_type": contribution_type, "explanation": explanation, "evidence": evidence,
          "version": CAUSAL_VERSION, "generated_at": generated_at})


def write_affected_component(tx, topic_slug, event_slug, component_node_id, explanation, evidence, generated_at):
    tx.run("""
        MATCH (e:Event {topic_slug: $topic_slug, slug: $event_slug})
        MATCH (c) WHERE elementId(c) = $component_node_id
        MERGE (e)-[r:AFFECTED_COMPONENT]->(c)
        SET r.explanation = $explanation, r.evidence = $evidence,
            r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"topic_slug": topic_slug, "event_slug": event_slug, "component_node_id": component_node_id,
          "explanation": explanation, "evidence": evidence, "version": CAUSAL_VERSION, "generated_at": generated_at})


def write_cross_topic_caused(tx, cause_ref, effect_ref, explanation, evidence, generated_at):
    tx.run("""
        MATCH (cause:Event {topic_slug: $cause_topic, slug: $cause_slug})
        MATCH (effect:Event {topic_slug: $effect_topic, slug: $effect_slug})
        MERGE (cause)-[r:CROSS_TOPIC_CAUSED]->(effect)
        SET r.explanation = $explanation, r.evidence = $evidence,
            r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"cause_topic": cause_ref[0], "cause_slug": cause_ref[1], "effect_topic": effect_ref[0],
          "effect_slug": effect_ref[1], "explanation": explanation, "evidence": evidence,
          "version": CAUSAL_VERSION, "generated_at": generated_at})


def delete_generated_relationships(tx):
    result = tx.run("""
        MATCH ()-[r]->() WHERE r.generated_by = $version
        DELETE r
        RETURN count(r) AS deleted
    """, {"version": CAUSAL_VERSION})
    return result.single()["deleted"]


def delete_generated_nodes(tx):
    result = tx.run("""
        MATCH (n) WHERE n.generated_by = $version
        DETACH DELETE n
        RETURN count(n) AS deleted
    """, {"version": CAUSAL_VERSION})
    return result.single()["deleted"]


# ---------------------------------------------------------------------------
# State reads.
# ---------------------------------------------------------------------------

def read_pipeline_state(tx):
    # Reads every stage's timestamp (not just this layer's own upstream) so
    # that staleness can be computed transitively. See `pipeline_staleness.py`.
    return _read_full_pipeline_state(tx)


def load_counts(tx):
    return {
        "root_causes": tx.run("MATCH (n:RootCause) RETURN count(n) AS c").single()["c"],
        "code_contributions": tx.run("MATCH ()-[r:CONTRIBUTED_TO]->() RETURN count(r) AS c").single()["c"],
        "affected_components": tx.run("MATCH ()-[r:AFFECTED_COMPONENT]->() RETURN count(r) AS c").single()["c"],
        "cross_topic_links": tx.run("MATCH ()-[r:CROSS_TOPIC_CAUSED]->() RETURN count(r) AS c").single()["c"],
        "within_topic_links": tx.run("MATCH ()-[r:CAUSED]->() RETURN count(r) AS c").single()["c"],
    }


def load_root_causes(tx):
    return [dict(row) for row in tx.run("""
        MATCH (rc:RootCause)
        OPTIONAL MATCH (e:Event)-[:HAS_ROOT_CAUSE]->(rc)
        OPTIONAL MATCH (rc)-[:ROOT_CAUSE_IN_COMPONENT]->(comp:Component)
        OPTIONAL MATCH (rc)-[:ROOT_CAUSE_EVIDENCED_BY]->(ev)
        WITH rc, collect(DISTINCT e.name) AS events, collect(DISTINCT comp.name) AS components,
             collect(DISTINCT coalesce(ev.display_name, ev.name, '')) AS evidence
        RETURN rc.slug AS slug, rc.name AS name, rc.cause_type AS cause_type, rc.summary AS summary,
               events, components, evidence
        ORDER BY rc.slug
    """)]


def load_code_contributions(tx):
    return [dict(row) for row in tx.run("""
        MATCH (c:CodeChange)-[r:CONTRIBUTED_TO]->(e:Event)-[:EVENT_OF_TOPIC]->(t:Topic)
        RETURN c.display_name AS code_change, c.file_path AS file_path, e.name AS event_name,
               t.name AS topic_name, r.contribution_type AS contribution_type,
               r.explanation AS explanation, r.evidence AS evidence
        ORDER BY t.name, e.occurred_at
    """)]


def load_affected_components(tx):
    return [dict(row) for row in tx.run("""
        MATCH (e:Event)-[r:AFFECTED_COMPONENT]->(c:Component)
        MATCH (e)-[:EVENT_OF_TOPIC]->(t:Topic)
        RETURN e.name AS event_name, t.name AS topic_name, c.name AS component_name,
               r.explanation AS explanation, r.evidence AS evidence
        ORDER BY t.name, e.occurred_at
    """)]


def load_cross_topic_links(tx):
    return [dict(row) for row in tx.run("""
        MATCH (cause:Event)-[r:CROSS_TOPIC_CAUSED]->(effect:Event)
        MATCH (cause)-[:EVENT_OF_TOPIC]->(ct:Topic)
        MATCH (effect)-[:EVENT_OF_TOPIC]->(et:Topic)
        RETURN cause.name AS cause_name, ct.name AS cause_topic, effect.name AS effect_name,
               et.name AS effect_topic, r.explanation AS explanation, r.evidence AS evidence
    """)]


def load_within_topic_links(tx):
    return [dict(row) for row in tx.run("""
        MATCH (cause:Event)-[r:CAUSED]->(effect:Event)
        MATCH (cause)-[:EVENT_OF_TOPIC]->(t:Topic)
        RETURN t.name AS topic_name, cause.name AS cause_name, effect.name AS effect_name,
               r.explanation AS explanation
        ORDER BY t.name, cause.occurred_at
    """)]


def load_causal_state(session):
    def _read(tx):
        return {
            "state": read_pipeline_state(tx),
            "counts": load_counts(tx),
            "root_causes": load_root_causes(tx),
            "code_contributions": load_code_contributions(tx),
            "affected_components": load_affected_components(tx),
            "cross_topic_links": load_cross_topic_links(tx),
            "within_topic_links": load_within_topic_links(tx),
        }

    result = session.execute_read(_read)
    state = result["state"]
    staleness = compute_staleness(state)["causal"]
    return {
        "last_layer_build_at": state["last_layer_build_at"],
        "last_architecture_build_at": state["last_architecture_build_at"],
        "last_causal_build_at": state["last_causal_build_at"],
        "needs_rerun": staleness["stale"],
        "stale_reasons": staleness["reasons"],
        "counts": result["counts"],
        "root_causes": result["root_causes"],
        "code_contributions": result["code_contributions"],
        "affected_components": result["affected_components"],
        "cross_topic_links": result["cross_topic_links"],
        "within_topic_links": result["within_topic_links"],
    }


def causal_state_payload(session):
    return load_causal_state(session)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def build_causal_layer(session):
    state = session.execute_read(read_pipeline_state)
    if not state.get("last_layer_build_at") or not state.get("last_architecture_build_at"):
        raise PrerequisiteError("Run Knowledge layer and Architecture layer first.")

    client = require_openai_client()
    generated_at = now_iso()

    session.execute_write(ensure_causal_constraints)

    topics = session.execute_read(load_topics)

    calls = 0
    input_tokens = 0
    output_tokens = 0
    has_token_usage = False
    total_discarded_evidence = 0
    topic_results = []
    all_cross_links = []

    for topic in topics:
        bundle = session.execute_read(assemble_causal_bundle, topic)

        response = call_model(client, bundle)
        calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            has_token_usage = True
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens

        parsed = response.output_parsed
        validated = apply_causal_validation(parsed, bundle)
        total_discarded_evidence += validated["discarded_evidence"]

        topic_results.append((topic, bundle, validated))
        all_cross_links.extend(validated["cross_topic_links"])

    deduped_cross_links = dedupe_cross_topic_links(all_cross_links)

    # Every model call succeeded. Now, and only now, replace the old layer.
    deleted_relationships = session.execute_write(delete_generated_relationships)
    deleted_nodes = session.execute_write(delete_generated_nodes)

    for topic, bundle, validated in topic_results:
        topic_slug = topic["slug"]

        for root_cause in validated["root_causes"]:
            session.execute_write(write_root_cause, root_cause, generated_at)
            for event_id in root_cause["explains_event_ids"]:
                event_ref = bundle["event_ref_by_id"][event_id]
                session.execute_write(
                    write_has_root_cause, event_ref[0], event_ref[1], root_cause["slug"],
                    root_cause["explanation"], root_cause["evidence"], generated_at,
                )
            for component_id in root_cause["component_ids"]:
                component_node_id = bundle["component_items"][component_id]["id"]
                session.execute_write(write_root_cause_in_component, root_cause["slug"], component_node_id, generated_at)

            evidence_ids = [
                bundle["node_id_by_identifier"][identifier]
                for identifier in root_cause["evidence"]
                if identifier in bundle["node_id_by_identifier"]
            ]
            session.execute_write(write_root_cause_evidenced_by, root_cause["slug"], evidence_ids, generated_at)

        for contribution in validated["code_contributions"]:
            code_change_node_id = bundle["code_change_items"][contribution["code_change_id"]]["id"]
            event_ref = bundle["event_ref_by_id"][contribution["event_id"]]
            session.execute_write(
                write_contributed_to, code_change_node_id, event_ref[0], event_ref[1],
                contribution["contribution_type"], contribution["explanation"], contribution["evidence"], generated_at,
            )

        for affected in validated["affected_components"]:
            event_ref = bundle["event_ref_by_id"][affected["event_id"]]
            component_node_id = bundle["component_items"][affected["component_id"]]["id"]
            session.execute_write(
                write_affected_component, event_ref[0], event_ref[1], component_node_id,
                affected["explanation"], affected["evidence"], generated_at,
            )

    event_ref_lookup = {}
    for _, bundle, _ in topic_results:
        event_ref_lookup.update(bundle["event_ref_by_id"])

    for link in deduped_cross_links:
        cause_ref = event_ref_lookup[link["cause_event_id"]]
        effect_ref = event_ref_lookup[link["effect_event_id"]]
        session.execute_write(write_cross_topic_caused, cause_ref, effect_ref, link["explanation"], link["evidence"], generated_at)

    session.execute_write(touch_pipeline_state, "last_causal_build_at", generated_at)

    report = load_causal_state(session)
    report.update({
        "built_at": generated_at,
        "deleted_relationships": deleted_relationships,
        "deleted_nodes": deleted_nodes,
        "calls": calls,
        "model": OPENAI_MODEL,
        "discarded_evidence": total_discarded_evidence,
        "token_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens} if has_token_usage else None,
    })
    return report
