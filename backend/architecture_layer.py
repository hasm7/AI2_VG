"""Architecture layer: deterministic code structure plus LLM-derived logical components.

This layer reads `CodeChange`/`PullRequest` nodes already in Neo4j and builds
two things with one button:

Part A (deterministic): `Repository` -> `Module` -> `File` structure, derived
purely from `CodeChange.file_path`, with `MODIFIES_FILE` edges back to the
code changes that touched each file.

Part B (LLM): one model call per `Repository` that proposes logical
`Component` nodes and `DEPENDS_ON` edges between them, grounded in the PRs,
code changes, reviews, related documents and messages for that repository.

Like the Knowledge layer, nothing the model returns is written until it has
been validated. Unlike the Knowledge layer, a failed model call must not
touch the existing layer at all: the old layer is only deleted after every
model call for this run has succeeded (see `build_architecture_layer`).

This module reads and writes Neo4j only. It never touches PostgreSQL.
"""

import json
from typing import Literal, Optional

from pydantic import BaseModel

from reference_extraction import now_iso, touch_pipeline_state
from topic_event_extraction import require_openai_client
from pipeline_staleness import compute_staleness
from pipeline_staleness import read_pipeline_state as _read_full_pipeline_state

OPENAI_MODEL = "gpt-5.6-terra"
OPENAI_REASONING_EFFORT = "medium"
ARCHITECTURE_VERSION = "architecture-layer-v1"

REFERENCE_EXTRACTOR_NAME = "reference-extraction-v1"
MAX_COMPONENTS = 20
MAX_DEPENDENCIES = 30
TRUNCATE_LENGTH = 4000


class PrerequisiteError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Model output schema.
# ---------------------------------------------------------------------------

class ComponentOut(BaseModel):
    slug: str
    name: str
    component_type: Literal[
        "service", "endpoint", "job", "data_store", "library", "ui", "external_system", "other"
    ]
    summary: str
    file_paths: list[str]
    evidence: list[str]


class DependencyOut(BaseModel):
    from_component_slug: str
    to_component_slug: str
    dependency_type: Literal["calls", "reads_from", "writes_to", "shares_logic_with", "depends_on"]
    explanation: str
    evidence: list[str]


class ArchitectureExtractionOut(BaseModel):
    components: list[ComponentOut]
    dependencies: list[DependencyOut]


ARCHITECTURE_INSTRUCTIONS = """\
You extract the logical components of one repository and the dependencies \
between them, from the pull requests, code changes, reviews, documents and \
messages given to you.

A component is a logical part of the system that has its own responsibility, \
for example an endpoint, a service, a background job, a data store, or a \
shared library. A single file is not a component unless it has its own \
clear responsibility.

Rules:
- Only propose components and dependencies that are supported by the bundle. \
Do not use general knowledge about how such systems usually work.
- Every component and every dependency must cite evidence identifiers from \
the bundle, copied exactly.
- `file_paths` on a component must be copied exactly from the given file list.
- Use short lowercase slugs with hyphens.
"""


def trunc(value):
    if value is None:
        return None
    return value[:TRUNCATE_LENGTH]


# ---------------------------------------------------------------------------
# Part A: deterministic code structure.
# ---------------------------------------------------------------------------

def module_path_of(file_path):
    if "/" not in file_path:
        return "(root)"
    return file_path.rsplit("/", 1)[0]


def file_name_of(file_path):
    return file_path.rsplit("/", 1)[-1]


def extension_of(file_name):
    if "." in file_name:
        return file_name.rsplit(".", 1)[-1]
    return ""


def load_code_changes(tx):
    rows = tx.run("""
        MATCH (pr:PullRequest)-[:HAS_CODE_CHANGE]->(c:CodeChange)
        RETURN elementId(c) AS id,
               coalesce(c.source_instance, pr.source_instance) AS source_instance,
               coalesce(c.repository, pr.repository) AS repository,
               c.file_path AS file_path
    """)
    return [dict(row) for row in rows]


def build_architecture_structures(code_changes):
    repos = {}
    modules = {}
    files = {}
    modifies_edges = []

    for change in code_changes:
        source_instance = change["source_instance"]
        repository = change["repository"]
        path = change["file_path"]

        repos.setdefault((source_instance, repository), {
            "source_instance": source_instance, "name": repository,
        })

        module_path = module_path_of(path)
        modules.setdefault((source_instance, repository, module_path), {
            "source_instance": source_instance, "repository": repository, "path": module_path,
            "display_name": f"{repository}:{module_path}",
        })

        file_key = (source_instance, repository, path)
        if file_key not in files:
            file_name = file_name_of(path)
            files[file_key] = {
                "source_instance": source_instance, "repository": repository, "path": path,
                "module_path": module_path, "file_name": file_name,
                "extension": extension_of(file_name), "change_count": 0,
            }
        files[file_key]["change_count"] += 1

        modifies_edges.append({
            "code_change_id": change["id"], "source_instance": source_instance,
            "repository": repository, "path": path,
        })

    return repos, modules, files, modifies_edges


def ensure_architecture_constraints(tx):
    tx.run("""
        CREATE CONSTRAINT repository_key IF NOT EXISTS
        FOR (n:Repository) REQUIRE (n.source_instance, n.name) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT module_key IF NOT EXISTS
        FOR (n:Module) REQUIRE (n.source_instance, n.repository, n.path) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT file_key IF NOT EXISTS
        FOR (n:File) REQUIRE (n.source_instance, n.repository, n.path) IS UNIQUE
    """)
    tx.run("""
        CREATE CONSTRAINT component_key IF NOT EXISTS
        FOR (n:Component) REQUIRE (n.source_instance, n.repository, n.slug) IS UNIQUE
    """)


def write_repositories(tx, repos, generated_at):
    if not repos:
        return
    tx.run("""
        UNWIND $repos AS repo
        MERGE (r:Repository {source_instance: repo.source_instance, name: repo.name})
        SET r.display_name = repo.name, r.derived = true,
            r.generated_by = $version, r.generated_at = $generated_at
    """, {"repos": repos, "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


def write_modules(tx, modules, generated_at):
    if not modules:
        return
    tx.run("""
        UNWIND $modules AS mod
        MATCH (r:Repository {source_instance: mod.source_instance, name: mod.repository})
        MERGE (m:Module {source_instance: mod.source_instance, repository: mod.repository, path: mod.path})
        SET m.display_name = mod.display_name, m.derived = true,
            m.generated_by = $version, m.generated_at = $generated_at
        MERGE (r)-[rel:CONTAINS_MODULE]->(m)
        SET rel.derived = true, rel.generated_by = $version, rel.generated_at = $generated_at
    """, {"modules": modules, "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


def write_files(tx, files, generated_at):
    if not files:
        return
    tx.run("""
        UNWIND $files AS f
        MATCH (m:Module {source_instance: f.source_instance, repository: f.repository, path: f.module_path})
        MERGE (file:File {source_instance: f.source_instance, repository: f.repository, path: f.path})
        SET file.file_name = f.file_name, file.extension = f.extension, file.change_count = f.change_count,
            file.display_name = f.path, file.derived = true,
            file.generated_by = $version, file.generated_at = $generated_at
        MERGE (m)-[rel:CONTAINS_FILE]->(file)
        SET rel.derived = true, rel.generated_by = $version, rel.generated_at = $generated_at
    """, {"files": files, "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


def write_modifies_file(tx, edges, generated_at):
    if not edges:
        return
    tx.run("""
        UNWIND $edges AS e
        MATCH (c) WHERE elementId(c) = e.code_change_id
        MATCH (file:File {source_instance: e.source_instance, repository: e.repository, path: e.path})
        MERGE (c)-[rel:MODIFIES_FILE]->(file)
        SET rel.derived = true, rel.generated_by = $version, rel.generated_at = $generated_at
    """, {"edges": edges, "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


# ---------------------------------------------------------------------------
# Part B: bundle assembly for one repository.
# ---------------------------------------------------------------------------

def load_repository_pull_requests(tx, source_instance, repository):
    return [dict(row) for row in tx.run("""
        MATCH (pr:PullRequest {source_instance: $si, repository: $repo})
        RETURN elementId(pr) AS id, pr.display_name AS display_name,
               pr.title AS title, pr.description AS description, pr.state AS state
    """, {"si": source_instance, "repo": repository})]


def load_repository_code_changes(tx, source_instance, repository):
    return [dict(row) for row in tx.run("""
        MATCH (pr:PullRequest {source_instance: $si, repository: $repo})-[:HAS_CODE_CHANGE]->(c:CodeChange)
        RETURN elementId(c) AS id, c.display_name AS display_name, c.file_path AS file_path,
               c.change_type AS change_type, c.before_summary AS before_summary,
               c.after_summary AS after_summary, c.diff AS diff, c.version_number AS version_number
    """, {"si": source_instance, "repo": repository})]


def load_repository_reviews(tx, source_instance, repository):
    return [dict(row) for row in tx.run("""
        MATCH (pr:PullRequest {source_instance: $si, repository: $repo})-[:HAS_PR_REVIEW]->(rv:PullRequestReview)
        RETURN elementId(rv) AS id, rv.source_id AS source_id, rv.display_name AS display_name, rv.body AS body
    """, {"si": source_instance, "repo": repository})]


def load_documents_via_mentions(tx, own_ids, extractor):
    if not own_ids:
        return []
    return [dict(row) for row in tx.run("""
        UNWIND $own_ids AS oid
        MATCH (own) WHERE elementId(own) = oid
        OPTIONAL MATCH (own)-[r1:MENTIONS_DOCUMENT]->(d1:Document) WHERE r1.extracted_by = $extractor
        OPTIONAL MATCH (d2:Document)-[r2:MENTIONS_DOCUMENT]->(own) WHERE r2.extracted_by = $extractor
        WITH collect(DISTINCT d1) + collect(DISTINCT d2) AS docs
        UNWIND docs AS d
        WITH DISTINCT d WHERE d IS NOT NULL
        RETURN elementId(d) AS id, d.document_id AS document_id, d.title AS title, d.body AS body
    """, {"own_ids": own_ids, "extractor": extractor})]


def load_technical_design_documents(tx):
    return [dict(row) for row in tx.run("""
        MATCH (d:Document {document_type: "technical-design"})
        RETURN elementId(d) AS id, d.document_id AS document_id, d.title AS title, d.body AS body
    """)]


def load_pr_mentioners(tx, pr_ids, extractor):
    """Nodes that mention a PR in this repository, plus documents they in turn mention."""
    if not pr_ids:
        return []
    return [dict(row) for row in tx.run("""
        UNWIND $pr_ids AS pid
        MATCH (pr) WHERE elementId(pr) = pid
        MATCH (x)-[r:MENTIONS_PULL_REQUEST]->(pr) WHERE r.extracted_by = $extractor
        OPTIONAL MATCH (x)-[r2:MENTIONS_DOCUMENT]->(d:Document) WHERE r2.extracted_by = $extractor
        RETURN DISTINCT elementId(x) AS id, labels(x) AS labels, properties(x) AS props,
               elementId(d) AS doc_id, d.document_id AS doc_document_id,
               d.title AS doc_title, d.body AS doc_body
    """, {"pr_ids": pr_ids, "extractor": extractor})]


def pr_identifier(props):
    return props.get("display_name")


def review_identifier(props):
    return props.get("source_id")


def code_change_identifier(props):
    return f'{props.get("display_name")} v{props.get("version_number")}'


def document_identifier(props):
    return props.get("document_id")


def slack_identifier(props):
    return f'{props.get("message_id")} v{props.get("version_number")}'


def mail_identifier(props):
    return props.get("message_id")


def teams_segment_identifier(props):
    return props.get("segment_id")


def assemble_repository_bundle(tx, source_instance, repository, file_paths):
    items = {}
    node_id_by_identifier = {}

    def add(identifier, node_id, label, display_name, text):
        if identifier in items:
            return
        items[identifier] = {"label": label, "display_name": display_name, "text": text}
        node_id_by_identifier[identifier] = node_id

    prs = load_repository_pull_requests(tx, source_instance, repository)
    for pr in prs:
        identifier = pr_identifier(pr)
        text = "\n".join(filter(None, [trunc(pr.get("title")), trunc(pr.get("description")), pr.get("state")]))
        add(identifier, pr["id"], "PullRequest", pr.get("display_name") or identifier, text)

    code_changes = load_repository_code_changes(tx, source_instance, repository)
    for change in code_changes:
        identifier = code_change_identifier(change)
        text = "\n".join(filter(None, [
            f'{change.get("file_path", "")} ({change.get("change_type", "")})',
            trunc(change.get("before_summary")), trunc(change.get("after_summary")), trunc(change.get("diff")),
        ]))
        add(identifier, change["id"], "CodeChange", identifier, text)

    reviews = load_repository_reviews(tx, source_instance, repository)
    for review in reviews:
        identifier = review_identifier(review)
        add(identifier, review["id"], "PullRequestReview", review.get("display_name") or identifier, trunc(review.get("body")))

    pr_ids = [pr["id"] for pr in prs]
    own_ids = pr_ids + [c["id"] for c in code_changes] + [r["id"] for r in reviews]

    for doc in load_documents_via_mentions(tx, own_ids, REFERENCE_EXTRACTOR_NAME):
        identifier = document_identifier(doc)
        text = "\n".join(filter(None, [trunc(doc.get("title")), trunc(doc.get("body"))]))
        add(identifier, doc["id"], "Document", doc.get("title") or identifier, text)

    for doc in load_technical_design_documents(tx):
        identifier = document_identifier(doc)
        text = "\n".join(filter(None, [trunc(doc.get("title")), trunc(doc.get("body"))]))
        add(identifier, doc["id"], "Document", doc.get("title") or identifier, text)

    mentioners = load_pr_mentioners(tx, pr_ids, REFERENCE_EXTRACTOR_NAME)
    seen_mentioner_ids = set()
    for row in mentioners:
        labels = row["labels"] or []
        label = labels[0] if labels else None
        props = row["props"]
        node_id = row["id"]

        if node_id not in seen_mentioner_ids and label in ("SlackMessage", "MailMessage", "TeamsTranscriptSegment"):
            seen_mentioner_ids.add(node_id)
            if label == "SlackMessage":
                identifier = slack_identifier(props)
                add(identifier, node_id, label, identifier, trunc(props.get("body")))
            elif label == "MailMessage":
                identifier = mail_identifier(props)
                text = "\n".join(filter(None, [trunc(props.get("subject")), trunc(props.get("body"))]))
                add(identifier, node_id, label, props.get("subject") or identifier, text)
            elif label == "TeamsTranscriptSegment":
                identifier = teams_segment_identifier(props)
                add(identifier, node_id, label, identifier, trunc(props.get("body")))

        if row["doc_id"] is not None:
            doc_identifier = row["doc_document_id"]
            text = "\n".join(filter(None, [trunc(row["doc_title"]), trunc(row["doc_body"])]))
            add(doc_identifier, row["doc_id"], "Document", row["doc_title"] or doc_identifier, text)

    return {
        "source_instance": source_instance,
        "repository": repository,
        "file_paths": sorted(file_paths),
        "items": items,
        "valid_identifiers": set(items.keys()),
        "node_id_by_identifier": node_id_by_identifier,
    }


def call_model(client, bundle):
    payload = {
        "repository": bundle["repository"],
        "file_paths": bundle["file_paths"],
        "evidence_bundle": [
            {"identifier": identifier, "label": item["label"], "display_name": item["display_name"], "text": item["text"]}
            for identifier, item in bundle["items"].items()
        ],
    }
    return client.responses.parse(
        model=OPENAI_MODEL,
        reasoning={"effort": OPENAI_REASONING_EFFORT},
        instructions=ARCHITECTURE_INSTRUCTIONS,
        input=json.dumps(payload, ensure_ascii=False, default=str),
        text_format=ArchitectureExtractionOut,
    )


def apply_architecture_validation(parsed, valid_identifiers, valid_file_paths):
    discarded_evidence = 0
    discarded_file_paths = 0

    kept_components = []
    seen_slugs = set()
    for component in parsed.components:
        kept_evidence = [e for e in component.evidence if e in valid_identifiers]
        discarded_evidence += len(component.evidence) - len(kept_evidence)
        kept_files = [f for f in component.file_paths if f in valid_file_paths]
        discarded_file_paths += len(component.file_paths) - len(kept_files)

        if not kept_evidence:
            continue
        if component.slug in seen_slugs:
            continue
        seen_slugs.add(component.slug)

        kept_components.append({
            "slug": component.slug, "name": component.name, "component_type": component.component_type,
            "summary": component.summary, "file_paths": kept_files, "evidence": kept_evidence,
        })
    kept_components = kept_components[:MAX_COMPONENTS]
    component_slugs = {c["slug"] for c in kept_components}

    kept_dependencies = []
    seen_dependency_keys = set()
    for dependency in parsed.dependencies:
        if dependency.from_component_slug not in component_slugs or dependency.to_component_slug not in component_slugs:
            continue
        if dependency.from_component_slug == dependency.to_component_slug:
            continue
        kept_evidence = [e for e in dependency.evidence if e in valid_identifiers]
        discarded_evidence += len(dependency.evidence) - len(kept_evidence)
        if not kept_evidence:
            continue
        key = (dependency.from_component_slug, dependency.to_component_slug, dependency.dependency_type)
        if key in seen_dependency_keys:
            continue
        seen_dependency_keys.add(key)

        kept_dependencies.append({
            "from_component_slug": dependency.from_component_slug, "to_component_slug": dependency.to_component_slug,
            "dependency_type": dependency.dependency_type, "explanation": dependency.explanation,
            "evidence": kept_evidence,
        })
    kept_dependencies = kept_dependencies[:MAX_DEPENDENCIES]

    return {
        "components": kept_components, "dependencies": kept_dependencies,
        "discarded_evidence": discarded_evidence, "discarded_file_paths": discarded_file_paths,
    }


def write_component(tx, props):
    tx.run("""
        MERGE (c:Component {source_instance: $source_instance, repository: $repository, slug: $slug})
        SET c.name = $name, c.display_name = $name, c.component_type = $component_type,
            c.summary = $summary, c.derived = true, c.generated_by = $version,
            c.model = $model, c.generated_at = $generated_at
    """, props)


def write_part_of_repository(tx, source_instance, repository, slug, generated_at):
    tx.run("""
        MATCH (c:Component {source_instance: $si, repository: $repo, slug: $slug})
        MATCH (r:Repository {source_instance: $si, name: $repo})
        MERGE (c)-[rel:PART_OF_REPOSITORY]->(r)
        SET rel.derived = true, rel.generated_by = $version, rel.generated_at = $generated_at
    """, {"si": source_instance, "repo": repository, "slug": slug,
          "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


def write_implemented_in(tx, source_instance, repository, slug, file_path, generated_at):
    tx.run("""
        MATCH (c:Component {source_instance: $si, repository: $repo, slug: $slug})
        MATCH (f:File {source_instance: $si, repository: $repo, path: $path})
        MERGE (c)-[rel:IMPLEMENTED_IN]->(f)
        SET rel.derived = true, rel.generated_by = $version, rel.generated_at = $generated_at
    """, {"si": source_instance, "repo": repository, "slug": slug, "path": file_path,
          "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


def write_component_evidenced_by(tx, source_instance, repository, slug, node_ids, generated_at):
    if not node_ids:
        return
    tx.run("""
        MATCH (c:Component {source_instance: $si, repository: $repo, slug: $slug})
        UNWIND $node_ids AS nid
        MATCH (n) WHERE elementId(n) = nid
        MERGE (c)-[rel:COMPONENT_EVIDENCED_BY]->(n)
        SET rel.derived = true, rel.generated_by = $version, rel.generated_at = $generated_at
    """, {"si": source_instance, "repo": repository, "slug": slug, "node_ids": node_ids,
          "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


def write_depends_on(tx, source_instance, repository, dependency, generated_at):
    tx.run("""
        MATCH (from:Component {source_instance: $si, repository: $repo, slug: $from_slug})
        MATCH (to:Component {source_instance: $si, repository: $repo, slug: $to_slug})
        MERGE (from)-[rel:DEPENDS_ON {dependency_type: $dependency_type}]->(to)
        SET rel.explanation = $explanation, rel.evidence = $evidence,
            rel.derived = true, rel.generated_by = $version, rel.generated_at = $generated_at
    """, {"si": source_instance, "repo": repository, "from_slug": dependency["from_component_slug"],
          "to_slug": dependency["to_component_slug"], "dependency_type": dependency["dependency_type"],
          "explanation": dependency["explanation"], "evidence": dependency["evidence"],
          "version": ARCHITECTURE_VERSION, "generated_at": generated_at})


def delete_generated_relationships(tx):
    result = tx.run("""
        MATCH ()-[r]->() WHERE r.generated_by = $version
        DELETE r
        RETURN count(r) AS deleted
    """, {"version": ARCHITECTURE_VERSION})
    return result.single()["deleted"]


def delete_generated_nodes(tx):
    result = tx.run("""
        MATCH (n) WHERE n.generated_by = $version
        DETACH DELETE n
        RETURN count(n) AS deleted
    """, {"version": ARCHITECTURE_VERSION})
    return result.single()["deleted"]


# ---------------------------------------------------------------------------
# State reads (shared by GET and POST responses).
# ---------------------------------------------------------------------------

def read_pipeline_state(tx):
    # Reads every stage's timestamp (not just this layer's own upstream) so
    # that staleness can be computed transitively. See `pipeline_staleness.py`.
    return _read_full_pipeline_state(tx)


def load_counts(tx):
    return {
        "repositories": tx.run("MATCH (r:Repository) RETURN count(r) AS c").single()["c"],
        "modules": tx.run("MATCH (m:Module) RETURN count(m) AS c").single()["c"],
        "files": tx.run("MATCH (f:File) RETURN count(f) AS c").single()["c"],
        "components": tx.run("MATCH (c:Component) RETURN count(c) AS c").single()["c"],
        "dependencies": tx.run("MATCH (:Component)-[d:DEPENDS_ON]->(:Component) RETURN count(d) AS c").single()["c"],
    }


def load_components(tx):
    return [dict(row) for row in tx.run("""
        MATCH (c:Component)
        OPTIONAL MATCH (c)-[:IMPLEMENTED_IN]->(f:File)
        OPTIONAL MATCH (c)-[:COMPONENT_EVIDENCED_BY]->(ev)
        WITH c, collect(DISTINCT f.path) AS files,
             collect(DISTINCT coalesce(ev.display_name, ev.name, '')) AS evidence
        RETURN c.repository AS repository, c.slug AS slug, c.name AS name,
               c.component_type AS component_type, c.summary AS summary, files, evidence
        ORDER BY c.repository, c.slug
    """)]


def load_dependencies(tx):
    return [dict(row) for row in tx.run("""
        MATCH (from:Component)-[d:DEPENDS_ON]->(to:Component)
        RETURN from.name AS from_name, to.name AS to_name, d.dependency_type AS dependency_type,
               d.explanation AS explanation, d.evidence AS evidence
        ORDER BY from.repository, from.name
    """)]


def load_files(tx):
    return [dict(row) for row in tx.run("""
        MATCH (f:File)
        OPTIONAL MATCH (m:Module)-[:CONTAINS_FILE]->(f)
        OPTIONAL MATCH (c:CodeChange)-[:MODIFIES_FILE]->(f)
        OPTIONAL MATCH (comp:Component)-[:IMPLEMENTED_IN]->(f)
        WITH f, m, collect(DISTINCT c.display_name) AS modified_by, collect(DISTINCT comp.name) AS components
        RETURN f.repository AS repository, m.path AS module, f.path AS path,
               f.change_count AS change_count, modified_by, components
        ORDER BY f.repository, m.path, f.path
    """)]


def load_architecture_state(session):
    def _read(tx):
        state = read_pipeline_state(tx)
        return {
            "state": state,
            "counts": load_counts(tx),
            "components": load_components(tx),
            "dependencies": load_dependencies(tx),
            "files": load_files(tx),
        }

    result = session.execute_read(_read)
    state = result["state"]
    staleness = compute_staleness(state)["architecture"]
    return {
        "last_import_at": state["last_import_at"],
        "last_extraction_at": state["last_extraction_at"],
        "last_architecture_build_at": state["last_architecture_build_at"],
        "needs_rerun": staleness["stale"],
        "stale_reasons": staleness["reasons"],
        "counts": result["counts"],
        "components": result["components"],
        "dependencies": result["dependencies"],
        "files": result["files"],
    }


def architecture_state_payload(session):
    return load_architecture_state(session)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def build_architecture_layer(session):
    state = session.execute_read(read_pipeline_state)
    if not state.get("last_extraction_at"):
        raise PrerequisiteError("Run Reference extraction first.")

    client = require_openai_client()
    generated_at = now_iso()

    session.execute_write(ensure_architecture_constraints)

    code_changes = session.execute_read(load_code_changes)
    repos, modules, files, modifies_edges = build_architecture_structures(code_changes)

    file_paths_by_repo = {}
    for (source_instance, repository, path) in files:
        file_paths_by_repo.setdefault((source_instance, repository), set()).add(path)

    calls = 0
    input_tokens = 0
    output_tokens = 0
    has_token_usage = False
    total_discarded_evidence = 0
    total_discarded_file_paths = 0
    repo_results = []

    for (source_instance, repository) in repos:
        file_paths = file_paths_by_repo.get((source_instance, repository), set())
        bundle = session.execute_read(assemble_repository_bundle, source_instance, repository, file_paths)

        response = call_model(client, bundle)
        calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            has_token_usage = True
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens

        parsed = response.output_parsed
        validated = apply_architecture_validation(parsed, bundle["valid_identifiers"], set(bundle["file_paths"]))
        total_discarded_evidence += validated["discarded_evidence"]
        total_discarded_file_paths += validated["discarded_file_paths"]

        repo_results.append((source_instance, repository, bundle, validated))

    # Every model call succeeded. Now, and only now, replace the old layer.
    deleted_relationships = session.execute_write(delete_generated_relationships)
    deleted_nodes = session.execute_write(delete_generated_nodes)

    session.execute_write(write_repositories, list(repos.values()), generated_at)
    session.execute_write(write_modules, list(modules.values()), generated_at)
    session.execute_write(write_files, list(files.values()), generated_at)
    session.execute_write(write_modifies_file, modifies_edges, generated_at)

    for source_instance, repository, bundle, validated in repo_results:
        for component in validated["components"]:
            session.execute_write(write_component, {
                "source_instance": source_instance, "repository": repository, "slug": component["slug"],
                "name": component["name"], "component_type": component["component_type"],
                "summary": component["summary"], "version": ARCHITECTURE_VERSION,
                "model": OPENAI_MODEL, "generated_at": generated_at,
            })
            session.execute_write(write_part_of_repository, source_instance, repository, component["slug"], generated_at)
            for file_path in component["file_paths"]:
                session.execute_write(write_implemented_in, source_instance, repository, component["slug"], file_path, generated_at)
            evidence_ids = [
                bundle["node_id_by_identifier"][identifier]
                for identifier in component["evidence"]
                if identifier in bundle["node_id_by_identifier"]
            ]
            session.execute_write(write_component_evidenced_by, source_instance, repository, component["slug"], evidence_ids, generated_at)

        for dependency in validated["dependencies"]:
            session.execute_write(write_depends_on, source_instance, repository, dependency, generated_at)

    session.execute_write(touch_pipeline_state, "last_architecture_build_at", generated_at)

    report = load_architecture_state(session)
    report.update({
        "built_at": generated_at,
        "deleted_relationships": deleted_relationships,
        "deleted_nodes": deleted_nodes,
        "calls": calls,
        "model": OPENAI_MODEL,
        "discarded_evidence": total_discarded_evidence,
        "discarded_file_paths": total_discarded_file_paths,
        "token_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens} if has_token_usage else None,
    })
    return report
