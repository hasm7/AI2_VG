"""Graph algorithm layer: precomputed metrics written onto existing nodes.

Uses `networkx` (no Neo4j Graph Data Science) to compute collaboration
network metrics and communities from `WORKS_WITH`, and bus-factor metrics
from `Expertise`. Results are written as plain properties on existing
`Person`, `Topic`, and `Component` nodes, using `algorithms_generated_by` /
`algorithms_generated_at` instead of the normal `derived`/`generated_by`
stamp, so this layer never overwrites the stamp those nodes already carry
from their own layer. The one new node label this layer creates, `Community`,
uses the normal stamping and is deleted by `generated_by` on rerun like any
other layer.

This module reads and writes Neo4j only. It never touches PostgreSQL.
"""

import networkx as nx

from reference_extraction import now_iso, touch_pipeline_state
from collaboration_layer import load_eligible_persons
from pipeline_staleness import compute_staleness
from pipeline_staleness import read_pipeline_state as _read_full_pipeline_state

ALGORITHMS_VERSION = "graph-algorithms-v1"


class PrerequisiteError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------

def load_works_with_edges(tx):
    return [dict(row) for row in tx.run("""
        MATCH (a:Person)-[r:WORKS_WITH]->(b:Person)
        RETURN a.person_key AS a, b.person_key AS b, r.weight AS weight
    """)]


def load_expertise_for_bus_factor(tx):
    return [dict(row) for row in tx.run("""
        MATCH (p:Person)-[:HAS_EXPERTISE]->(e:Expertise)
        RETURN e.subject_label AS subject_label, e.subject_key AS subject_key,
               e.share AS share, e.rank AS rank, p.name AS person_name
    """)]


def load_all_topics(tx):
    return [dict(row) for row in tx.run("MATCH (t:Topic) RETURN t.slug AS slug")]


def load_all_components(tx):
    return [dict(row) for row in tx.run("""
        MATCH (c:Component)
        RETURN elementId(c) AS id, c.source_instance AS source_instance,
               c.repository AS repository, c.slug AS slug
    """)]


def load_component_dependency_counts(tx):
    return [dict(row) for row in tx.run("""
        MATCH (c:Component)
        OPTIONAL MATCH (c)-[:DEPENDS_ON]->(d)
        OPTIONAL MATCH (c)<-[:DEPENDS_ON]-(dd)
        OPTIONAL MATCH (ev:Event)-[:AFFECTED_COMPONENT]->(c)
        RETURN elementId(c) AS id, count(DISTINCT d) AS depends_on_count,
               count(DISTINCT dd) AS depended_on_by_count, count(DISTINCT ev) AS affected_event_count
    """)]


# ---------------------------------------------------------------------------
# Computation.
# ---------------------------------------------------------------------------

def build_collaboration_graph(eligible_persons, works_with_edges):
    graph = nx.Graph()
    for person in eligible_persons:
        graph.add_node(person["person_key"])
    for edge in works_with_edges:
        if edge["a"] in graph and edge["b"] in graph:
            graph.add_edge(edge["a"], edge["b"], weight=edge["weight"], distance=1.0 / edge["weight"])
    return graph


def compute_collaboration_metrics(graph):
    weighted_degree = dict(graph.degree(weight="weight"))
    betweenness = nx.betweenness_centrality(graph, weight="distance", normalized=True)
    communities = nx.algorithms.community.louvain_communities(graph, weight="weight", seed=42)
    communities_sorted = sorted(communities, key=lambda community: (-len(community), min(community) if community else ""))

    community_rows = []
    community_id_by_person = {}
    for index, community in enumerate(communities_sorted, start=1):
        community_id = f"collab-{index}"
        members = sorted(community)
        community_rows.append({"community_id": community_id, "size": len(members), "members": members})
        for person_key in members:
            community_id_by_person[person_key] = community_id

    person_rows = [
        {
            "person_key": person_key,
            "collab_weighted_degree": weighted_degree.get(person_key, 0),
            "collab_betweenness": round(betweenness.get(person_key, 0.0), 4),
            "community_id": community_id_by_person.get(person_key),
        }
        for person_key in graph.nodes
    ]
    return person_rows, community_rows


def compute_bus_factor(entries):
    entries_sorted = sorted(entries, key=lambda entry: -entry["share"])
    cumulative = 0.0
    bus_factor = 0
    for entry in entries_sorted:
        cumulative += entry["share"]
        bus_factor += 1
        if cumulative >= 0.5:
            break

    expert_count = len(entries_sorted)
    top_expert = next((entry["person_name"] for entry in entries_sorted if entry["rank"] == 1), None)
    if top_expert is None and entries_sorted:
        top_expert = entries_sorted[0]["person_name"]

    return bus_factor, expert_count, top_expert


def compute_topic_bus_factor_rows(expertise_rows, all_topics):
    by_subject = {}
    for row in expertise_rows:
        by_subject.setdefault((row["subject_label"], row["subject_key"]), []).append(row)

    rows = []
    for topic in all_topics:
        entries = by_subject.get(("Topic", topic["slug"]), [])
        bus_factor, expert_count, top_expert = compute_bus_factor(entries)
        rows.append({"slug": topic["slug"], "bus_factor": bus_factor, "expert_count": expert_count, "top_expert": top_expert})
    return rows


def compute_component_bus_factor(expertise_rows, all_components):
    by_subject = {}
    for row in expertise_rows:
        by_subject.setdefault((row["subject_label"], row["subject_key"]), []).append(row)

    bus_by_id = {}
    for component in all_components:
        subject_key = f'{component["source_instance"]}/{component["repository"]}/{component["slug"]}'
        entries = by_subject.get(("Component", subject_key), [])
        bus_factor, expert_count, top_expert = compute_bus_factor(entries)
        bus_by_id[component["id"]] = {"bus_factor": bus_factor, "expert_count": expert_count, "top_expert": top_expert}
    return bus_by_id


def merge_component_metrics(bus_by_id, dependency_rows):
    dependency_by_id = {row["id"]: row for row in dependency_rows}
    merged = []
    for component_id, bus in bus_by_id.items():
        dependency = dependency_by_id.get(
            component_id, {"depends_on_count": 0, "depended_on_by_count": 0, "affected_event_count": 0},
        )
        merged.append({
            "id": component_id, "bus_factor": bus["bus_factor"], "expert_count": bus["expert_count"],
            "top_expert": bus["top_expert"], "depends_on_count": dependency["depends_on_count"],
            "depended_on_by_count": dependency["depended_on_by_count"],
            "affected_event_count": dependency["affected_event_count"],
        })
    return merged


# ---------------------------------------------------------------------------
# Writes.
# ---------------------------------------------------------------------------

def remove_person_algorithm_props(tx):
    tx.run("""
        MATCH (p:Person)
        REMOVE p.collab_weighted_degree, p.collab_betweenness, p.community_id,
               p.algorithms_generated_by, p.algorithms_generated_at
    """)


def remove_topic_algorithm_props(tx):
    tx.run("""
        MATCH (t:Topic)
        REMOVE t.bus_factor, t.expert_count, t.top_expert, t.algorithms_generated_by, t.algorithms_generated_at
    """)


def remove_component_algorithm_props(tx):
    tx.run("""
        MATCH (c:Component)
        REMOVE c.bus_factor, c.expert_count, c.top_expert, c.depends_on_count, c.depended_on_by_count,
               c.affected_event_count, c.algorithms_generated_by, c.algorithms_generated_at
    """)


def ensure_community_constraint(tx):
    tx.run("""
        CREATE CONSTRAINT community_key IF NOT EXISTS
        FOR (n:Community) REQUIRE n.community_id IS UNIQUE
    """)


def write_community(tx, community_id, size, generated_at):
    tx.run("""
        MERGE (c:Community {community_id: $community_id})
        SET c.size = $size, c.display_name = $community_id, c.derived = true,
            c.generated_by = $version, c.generated_at = $generated_at
    """, {"community_id": community_id, "size": size, "version": ALGORITHMS_VERSION, "generated_at": generated_at})


def write_member_of_community(tx, person_key, community_id, generated_at):
    tx.run("""
        MATCH (p:Person {person_key: $person_key})
        MATCH (c:Community {community_id: $community_id})
        MERGE (p)-[r:MEMBER_OF_COMMUNITY]->(c)
        SET r.derived = true, r.generated_by = $version, r.generated_at = $generated_at
    """, {"person_key": person_key, "community_id": community_id, "version": ALGORITHMS_VERSION, "generated_at": generated_at})


def write_person_metrics(tx, rows, generated_at):
    if not rows:
        return
    tx.run("""
        UNWIND $rows AS row
        MATCH (p:Person {person_key: row.person_key})
        SET p.collab_weighted_degree = row.collab_weighted_degree,
            p.collab_betweenness = row.collab_betweenness, p.community_id = row.community_id,
            p.algorithms_generated_by = $version, p.algorithms_generated_at = $generated_at
    """, {"rows": rows, "version": ALGORITHMS_VERSION, "generated_at": generated_at})


def write_topic_metrics(tx, rows, generated_at):
    if not rows:
        return
    tx.run("""
        UNWIND $rows AS row
        MATCH (t:Topic {slug: row.slug})
        SET t.bus_factor = row.bus_factor, t.expert_count = row.expert_count, t.top_expert = row.top_expert,
            t.algorithms_generated_by = $version, t.algorithms_generated_at = $generated_at
    """, {"rows": rows, "version": ALGORITHMS_VERSION, "generated_at": generated_at})


def write_component_metrics(tx, rows, generated_at):
    if not rows:
        return
    tx.run("""
        UNWIND $rows AS row
        MATCH (c) WHERE elementId(c) = row.id
        SET c.bus_factor = row.bus_factor, c.expert_count = row.expert_count, c.top_expert = row.top_expert,
            c.depends_on_count = row.depends_on_count, c.depended_on_by_count = row.depended_on_by_count,
            c.affected_event_count = row.affected_event_count,
            c.algorithms_generated_by = $version, c.algorithms_generated_at = $generated_at
    """, {"rows": rows, "version": ALGORITHMS_VERSION, "generated_at": generated_at})


def delete_generated_relationships(tx):
    result = tx.run("""
        MATCH ()-[r]->() WHERE r.generated_by = $version
        DELETE r
        RETURN count(r) AS deleted
    """, {"version": ALGORITHMS_VERSION})
    return result.single()["deleted"]


def delete_generated_nodes(tx):
    result = tx.run("""
        MATCH (n) WHERE n.generated_by = $version
        DETACH DELETE n
        RETURN count(n) AS deleted
    """, {"version": ALGORITHMS_VERSION})
    return result.single()["deleted"]


# ---------------------------------------------------------------------------
# State reads.
# ---------------------------------------------------------------------------

def read_pipeline_state(tx):
    # Reads every stage's timestamp (not just this layer's own upstream) so
    # that staleness can be computed transitively. See `pipeline_staleness.py`.
    return _read_full_pipeline_state(tx)


# Eligibility rule duplicated from `collaboration_layer.load_eligible_persons`:
# only these persons ever get `collab_*`/`community_id` properties written,
# so counts and rows here must be scoped the same way.
ELIGIBLE_PERSON_WHERE = "coalesce(p.actor_type, '') <> 'mailbox' AND coalesce(p.identity_ambiguous, false) <> true"


def load_counts(tx):
    return {
        "communities": tx.run("MATCH (c:Community) RETURN count(c) AS c").single()["c"],
        "persons": tx.run(f"MATCH (p:Person) WHERE {ELIGIBLE_PERSON_WHERE} RETURN count(p) AS c").single()["c"],
        "topics": tx.run("MATCH (t:Topic) RETURN count(t) AS c").single()["c"],
        "components": tx.run("MATCH (c:Component) RETURN count(c) AS c").single()["c"],
    }


def load_person_rows(tx):
    return [dict(row) for row in tx.run(f"""
        MATCH (p:Person) WHERE {ELIGIBLE_PERSON_WHERE}
        RETURN p.name AS name, p.collab_weighted_degree AS collab_weighted_degree,
               p.collab_betweenness AS collab_betweenness, p.community_id AS community_id
        ORDER BY p.collab_betweenness DESC
    """)]


def load_community_rows(tx):
    return [dict(row) for row in tx.run("""
        MATCH (c:Community)
        OPTIONAL MATCH (p:Person)-[:MEMBER_OF_COMMUNITY]->(c)
        WITH c, collect(DISTINCT p.name) AS members
        RETURN c.community_id AS community_id, c.size AS size, members
        ORDER BY c.community_id
    """)]


def load_bus_factor_rows(tx):
    topics = [dict(row) for row in tx.run("""
        MATCH (t:Topic)
        RETURN 'Topic' AS subject_label, t.name AS subject_name, t.bus_factor AS bus_factor,
               t.expert_count AS expert_count, t.top_expert AS top_expert
    """)]
    components = [dict(row) for row in tx.run("""
        MATCH (c:Component)
        RETURN 'Component' AS subject_label, c.name AS subject_name, c.bus_factor AS bus_factor,
               c.expert_count AS expert_count, c.top_expert AS top_expert
    """)]
    rows = topics + components
    rows.sort(key=lambda row: (row["bus_factor"] if row["bus_factor"] is not None else -1, row["subject_name"] or ""))
    return rows


def load_component_rows(tx):
    return [dict(row) for row in tx.run("""
        MATCH (c:Component)
        RETURN c.name AS name, c.depends_on_count AS depends_on_count,
               c.depended_on_by_count AS depended_on_by_count,
               c.affected_event_count AS affected_event_count, c.bus_factor AS bus_factor
        ORDER BY c.name
    """)]


def load_algorithms_state(session):
    def _read(tx):
        return {
            "state": read_pipeline_state(tx),
            "counts": load_counts(tx),
            "persons": load_person_rows(tx),
            "communities": load_community_rows(tx),
            "bus_factor": load_bus_factor_rows(tx),
            "components": load_component_rows(tx),
        }

    result = session.execute_read(_read)
    state = result["state"]
    staleness = compute_staleness(state)["algorithms"]
    return {
        "last_layer_build_at": state["last_layer_build_at"],
        "last_architecture_build_at": state["last_architecture_build_at"],
        "last_causal_build_at": state["last_causal_build_at"],
        "last_collaboration_build_at": state["last_collaboration_build_at"],
        "last_algorithms_run_at": state["last_algorithms_run_at"],
        "needs_rerun": staleness["stale"],
        "stale_reasons": staleness["reasons"],
        "counts": result["counts"],
        "persons": result["persons"],
        "communities": result["communities"],
        "bus_factor": result["bus_factor"],
        "components": result["components"],
    }


def algorithms_state_payload(session):
    return load_algorithms_state(session)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def run_algorithms(session):
    state = session.execute_read(read_pipeline_state)
    if not state.get("last_collaboration_build_at"):
        raise PrerequisiteError("Run Collaboration layer first.")

    run_at = now_iso()

    eligible_persons = session.execute_read(load_eligible_persons)
    works_with_edges = session.execute_read(load_works_with_edges)
    graph = build_collaboration_graph(eligible_persons, works_with_edges)
    person_rows, community_rows = compute_collaboration_metrics(graph)

    expertise_rows = session.execute_read(load_expertise_for_bus_factor)
    all_topics = session.execute_read(load_all_topics)
    all_components = session.execute_read(load_all_components)
    topic_rows = compute_topic_bus_factor_rows(expertise_rows, all_topics)
    component_bus_by_id = compute_component_bus_factor(expertise_rows, all_components)

    dependency_rows = session.execute_read(load_component_dependency_counts)
    component_rows = merge_component_metrics(component_bus_by_id, dependency_rows)

    session.execute_write(remove_person_algorithm_props)
    session.execute_write(remove_topic_algorithm_props)
    session.execute_write(remove_component_algorithm_props)
    deleted_relationships = session.execute_write(delete_generated_relationships)
    deleted_nodes = session.execute_write(delete_generated_nodes)

    session.execute_write(ensure_community_constraint)
    for community in community_rows:
        session.execute_write(write_community, community["community_id"], community["size"], run_at)
        for person_key in community["members"]:
            session.execute_write(write_member_of_community, person_key, community["community_id"], run_at)

    session.execute_write(write_person_metrics, person_rows, run_at)
    session.execute_write(write_topic_metrics, topic_rows, run_at)
    session.execute_write(write_component_metrics, component_rows, run_at)

    session.execute_write(touch_pipeline_state, "last_algorithms_run_at", run_at)

    report = load_algorithms_state(session)
    report.update({
        "run_at": run_at,
        "deleted_relationships": deleted_relationships,
        "deleted_nodes": deleted_nodes,
    })
    return report
