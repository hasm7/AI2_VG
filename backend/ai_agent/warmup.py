"""Warms Neo4j up for the agent when the backend starts. Read only, no model call.

The first run of a query after Neo4j starts is slow (the query is planned and its data read from disk), up to several
seconds per query. Run once here, the plans and the data are in memory before the first question, so the first answer
is as quick as the rest. The entry searches and every specialist run once, on a real node's embedding and the nodes it
finds.
"""

import threading
import time

from neo4j import GraphDatabase

from .db import read
from .retrieval import FULLTEXT_SEARCH, LOOKUP_DOCUMENTS, LOOKUP_ENTITY, LOOKUP_ISSUES, LOOKUP_PULL_REQUESTS, VECTOR_SEARCH
from .settings import load_settings
from .specialists import SPECIALIST_FUNCTIONS, run_specialist

SAMPLE_VECTOR = "MATCH (n:Searchable) WHERE n.embedding IS NOT NULL RETURN n.embedding AS vector LIMIT 1"
SAMPLE_CHUNK = "MATCH (c:EmbeddingChunk)-[:CHUNK_OF]->(n) RETURN elementId(n) AS node, elementId(c) AS chunk LIMIT 1"
# A plan that takes every branch in the specialists (the full list of persons, the rankings).
WARMUP_PLAN = {"question_types": ["who", "ranking", "why", "timeline"], "entities": [], "keywords_en": []}
WARMUP_TIMEOUT_SECONDS = 120  # generous: this run is the slow one
RETRY_SECONDS = 5
MAX_WAIT_SECONDS = 300  # how long to wait for Neo4j to accept connections


def warm_up(uri: str, user: str, password: str, database: str) -> float:
    """Runs the entry searches and every specialist once. Returns the seconds it took."""
    started = time.perf_counter()
    settings = load_settings()
    settings = {**settings, "query_timeout_seconds": WARMUP_TIMEOUT_SECONDS}
    search = settings["search"]
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database, default_access_mode="READ") as session:
            rows = read(session, SAMPLE_VECTOR, None, WARMUP_TIMEOUT_SECONDS)
            if not rows:
                return time.perf_counter() - started
            vector = rows[0]["vector"]
            hits = read(session, VECTOR_SEARCH, {"vector": vector, "k": search["vector_k"]}, WARMUP_TIMEOUT_SECONDS)
            read(session, FULLTEXT_SEARCH, {"q": '"project"', "k": search["fulltext_k"]}, WARMUP_TIMEOUT_SECONDS)
            read(session, LOOKUP_ENTITY, {"q": '"project"', "k": search["lookup_k"]}, WARMUP_TIMEOUT_SECONDS)
            read(session, LOOKUP_ISSUES, {"keys": []}, WARMUP_TIMEOUT_SECONDS)
            read(session, LOOKUP_PULL_REQUESTS, {"refs": []}, WARMUP_TIMEOUT_SECONDS)
            read(session, LOOKUP_DOCUMENTS, {"ids": []}, WARMUP_TIMEOUT_SECONDS)
            chunk = read(session, SAMPLE_CHUNK, None, WARMUP_TIMEOUT_SECONDS)
        entry_ids = list(dict.fromkeys(row["id"] for row in hits))[: search["entry_points"]]
        chunk_ids_by_node = {chunk[0]["node"]: [chunk[0]["chunk"]]} if chunk else None
        if chunk:
            entry_ids.append(chunk[0]["node"])
        for name in SPECIALIST_FUNCTIONS:
            run_specialist(name, driver, database, entry_ids, WARMUP_PLAN, settings,
                           question_vector=vector, chunk_ids_by_node=chunk_ids_by_node)
    return time.perf_counter() - started


def start_warmup(uri: str, user: str, password: str, database: str) -> None:
    """Warms up in a background thread, waiting for Neo4j if it is still starting. Never stops the backend."""
    def run():
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while True:
            try:
                seconds = warm_up(uri, user, password, database)
                print(f"Agent warm-up done in {seconds:.1f} s.", flush=True)
                return
            except Exception as error:  # noqa: BLE001 - Neo4j not up yet, or any other reason: the agent still works
                if time.monotonic() > deadline:
                    print(f"Agent warm-up skipped: {error}", flush=True)
                    return
                time.sleep(RETRY_SECONDS)

    threading.Thread(target=run, name="agent-warmup", daemon=True).start()
