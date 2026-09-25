"""Entry step: find the nodes a question enters the graph through. Code only, no model call except one embedding.

Three lists are merged with reciprocal rank fusion (RRF):
- lookup: exact keys (issue keys, PR references, document ids) and names through `entity_lookup`;
- vector: the question's embedding against `searchable_embedding` (meaning, works across languages);
- fulltext: English keywords against `searchable_text` (exact words the vector can miss).
Versions of one source share an `embedding_group`; only the best hit per group is kept.
"""

import re

from embedding_pass import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL

from .db import NODE_LABEL, NODE_NAME, read
from .usage import embedding_usage

ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
PR_PATTERN = re.compile(r"\b[a-z0-9][a-z0-9._-]*#\d+\b")
DOCUMENT_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]{3,}$")

RRF_K = 60
LOOKUP_WEIGHT = 2.0  # an exact key or name named in the question outranks a similar-sounding search hit

_RETURN_NODE = f"""
RETURN elementId(n) AS id, {NODE_LABEL} AS label, {NODE_NAME} AS name,
       coalesce(n.embedding_group, 'node:' + elementId(n)) AS grp
"""

LOOKUP_ISSUES = "MATCH (n:Issue) WHERE n.issue_key IN $keys" + _RETURN_NODE
LOOKUP_PULL_REQUESTS = "MATCH (n:PullRequest) WHERE n.display_name IN $refs" + _RETURN_NODE
LOOKUP_DOCUMENTS = (
    "MATCH (n:Document) WHERE n.document_id IN $ids OR any(k IN $ids WHERE n.title STARTS WITH k + ':')" + _RETURN_NODE
)
LOOKUP_ENTITY = (
    "CALL db.index.fulltext.queryNodes('entity_lookup', $q, {limit: $k}) YIELD node AS n, score" + _RETURN_NODE
)

# A hit on a chunk of a long text stands for its source node.
VECTOR_SEARCH = f"""
MATCH (hit:Searchable)
  SEARCH hit IN (VECTOR INDEX searchable_embedding FOR $vector LIMIT $k) SCORE AS score
OPTIONAL MATCH (hit)-[:CHUNK_OF]->(source)
WITH coalesce(source, hit) AS n, score
{_RETURN_NODE}, score
ORDER BY score DESC
"""

FULLTEXT_SEARCH = f"""
CALL db.index.fulltext.queryNodes('searchable_text', $q, {{limit: $k}}) YIELD node AS hit, score
OPTIONAL MATCH (hit)-[:CHUNK_OF]->(source)
WITH coalesce(source, hit) AS n, score
{_RETURN_NODE}, score
ORDER BY score DESC
"""


def _quote(term: str) -> str:
    """A Lucene phrase; inside quotes only the quote and the backslash need escaping."""
    return '"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _phrase_query(terms: list[str]) -> str:
    return " OR ".join(_quote(term) for term in terms if term.strip())


def detect_references(question: str, entities: list[str]) -> dict[str, list[str]]:
    """Issue keys, PR references and document ids, from the question itself and from the planner's entities."""
    text = " ".join([question, *entities])
    issue_keys = sorted(set(ISSUE_KEY_PATTERN.findall(text)))
    pull_requests = sorted(set(PR_PATTERN.findall(text)))
    document_ids = sorted({e.strip() for e in entities if DOCUMENT_ID_PATTERN.match(e.strip())} - set(issue_keys))
    keyed = set(issue_keys) | set(pull_requests) | set(document_ids)
    names = [e.strip() for e in entities if e.strip() and e.strip() not in keyed]
    return {"issue_keys": issue_keys, "pull_requests": pull_requests, "document_ids": document_ids, "names": names}


def _embed(client, settings: dict, text: str) -> tuple[list[float], dict]:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text, dimensions=EMBEDDING_DIMENSIONS)
    return response.data[0].embedding, embedding_usage(settings, "entry", EMBEDDING_MODEL, response)


def _fuse(ranked_lists: list[tuple[str, float, list[dict]]]) -> list[dict]:
    """Reciprocal rank fusion, then one hit per `embedding_group` (the best one)."""
    fused: dict[str, dict] = {}
    for via, weight, rows in ranked_lists:
        for rank, row in enumerate(rows):
            entry = fused.setdefault(row["id"], {**{k: row[k] for k in ("id", "label", "name", "grp")}, "score": 0.0, "via": []})
            entry["score"] += weight / (RRF_K + rank + 1)
            if via not in entry["via"]:
                entry["via"].append(via)

    best_per_group: dict[str, dict] = {}
    for entry in sorted(fused.values(), key=lambda e: e["score"], reverse=True):
        best_per_group.setdefault(entry["grp"], entry)
    return list(best_per_group.values())


def find_entry_points(session, client, question: str, plan: dict, settings: dict) -> tuple[list[dict], list[dict], dict]:
    """Returns (entry_points, usage_entries, details). `details` lists what each search found, for the trace."""
    search = settings["search"]
    timeout = settings["query_timeout_seconds"]
    refs = detect_references(question, plan.get("entities", []))

    lookup_rows: list[dict] = []
    if refs["issue_keys"]:
        lookup_rows += read(session, LOOKUP_ISSUES, {"keys": refs["issue_keys"]}, timeout)
    if refs["pull_requests"]:
        lookup_rows += read(session, LOOKUP_PULL_REQUESTS, {"refs": refs["pull_requests"]}, timeout)
    if refs["document_ids"]:
        lookup_rows += read(session, LOOKUP_DOCUMENTS, {"ids": refs["document_ids"]}, timeout)
    for name in refs["names"]:
        lookup_rows += read(session, LOOKUP_ENTITY, {"q": _quote(name), "k": search["lookup_k"]}, timeout)

    vector, usage_entry = _embed(client, settings, question)
    vector_rows = read(session, VECTOR_SEARCH, {"vector": vector, "k": search["vector_k"]}, timeout)

    fulltext_terms = list(plan.get("keywords_en", [])) + refs["issue_keys"] + refs["pull_requests"] + refs["names"]
    fulltext_rows = []
    if _phrase_query(fulltext_terms):
        fulltext_rows = read(session, FULLTEXT_SEARCH, {"q": _phrase_query(fulltext_terms), "k": search["fulltext_k"]}, timeout)

    fused = _fuse([
        ("lookup", LOOKUP_WEIGHT, lookup_rows),
        ("vector", 1.0, vector_rows),
        ("fulltext", 1.0, fulltext_rows),
    ])
    entry_points = [
        {"id": e["id"], "label": e["label"], "name": e["name"], "score": round(e["score"], 5), "via": e["via"]}
        for e in fused[: search["entry_points"]]
    ]
    details = {
        "references": refs,
        "lookup_hits": len(lookup_rows),
        "vector_hits": len(vector_rows),
        "fulltext_hits": len(fulltext_rows),
        "fulltext_query": _phrase_query(fulltext_terms),
    }
    return entry_points, [usage_entry], details
