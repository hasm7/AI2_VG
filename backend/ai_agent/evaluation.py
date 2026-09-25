"""Runs the test questions through the agent and scores them in code (no model is used to judge).

A question passes when the route is the expected one, every expected evidence group is found in the evidence, every
expected fact group is found in the facts, and every expected term group appears in the answer. Citations are
reported too (whether an expected node was also cited), but do not decide the pass.

Every question runs on its own, without conversation history, so the results do not depend on the order.
The last run is saved to `evaluation_last.json`, and a one-line summary per run to `evaluation_history.json`.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from neo4j import GraphDatabase

from .graph import get_compiled_graph
from .settings import load_settings
from .usage import total_cost

HERE = Path(__file__).parent
QUESTIONS_PATH = HERE / "test_questions.json"
LAST_RUN_PATH = HERE / "evaluation_last.json"
HISTORY_PATH = HERE / "evaluation_history.json"
HISTORY_LIMIT = 50


def load_questions() -> list[dict]:
    return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def evaluation_state() -> dict:
    return {
        "questions": load_questions(),
        "last_run": _read_json(LAST_RUN_PATH, None),
        "history": _read_json(HISTORY_PATH, []),
    }


def _matches(spec: dict, label: str, name: str) -> bool:
    if spec.get("label") and spec["label"] != label:
        return False
    if "name" in spec:
        return (name or "") == spec["name"]
    if "contains" in spec:
        return spec["contains"].lower() in (name or "").lower()
    return True


def _score(question: dict, state: dict) -> dict:
    evidence_nodes = [node for packet in state.get("evidence") or [] for node in packet["nodes"]]
    facts = [fact for packet in state.get("evidence") or [] for fact in packet["facts"]]
    citations = state.get("citations") or []
    answer = state.get("final_answer") or ""

    evidence_groups = []
    for group in question.get("expected_evidence", []):
        found = [n["name"] for n in evidence_nodes if any(_matches(s, n["label"], n["name"]) for s in group)]
        cited = [c["display_name"] for c in citations if any(_matches(s, c["label"], c["display_name"]) for s in group)]
        evidence_groups.append({"expected": group, "found": sorted(set(found)), "cited": sorted(set(cited))})

    fact_groups = [
        {"expected": group, "found": any(variant.lower() in fact.lower() for fact in facts for variant in group)}
        for group in question.get("expected_facts", [])
    ]
    term_groups = [
        {"expected": group, "found": any(variant.lower() in answer.lower() for variant in group)}
        for group in question.get("expected_terms", [])
    ]

    route = (state.get("plan") or {}).get("route")
    route_ok = route == question.get("expected_route", "graph")
    evidence_ok = all(group["found"] for group in evidence_groups)
    facts_ok = all(group["found"] for group in fact_groups)
    terms_ok = all(group["found"] for group in term_groups)
    usage = state.get("usage") or []
    return {
        "route": route,
        "route_ok": route_ok,
        "evidence": evidence_groups,
        "evidence_ok": evidence_ok,
        "evidence_cited": all(group["cited"] for group in evidence_groups),
        "facts": fact_groups,
        "facts_ok": facts_ok,
        "terms": term_groups,
        "terms_ok": terms_ok,
        "passed": route_ok and evidence_ok and facts_ok and terms_ok,
        "specialists": (state.get("plan") or {}).get("specialists", []),
        "question_types": (state.get("plan") or {}).get("question_types", []),
        "explorer_ran": bool(state.get("explorer_ran")),
        "sufficiency": state.get("sufficiency"),
        "errors": state.get("errors") or [],
        "answer": answer,
        "citations": [c["display_name"] for c in citations],
        "cost_usd": total_cost(usage),
        "input_tokens": sum(entry["input_tokens"] for entry in usage),
        "output_tokens": sum(entry["output_tokens"] for entry in usage),
        "model_calls": len([entry for entry in usage if entry["node"] != "entry"]),  # `entry` is the embedding call
    }


def run_evaluation(client, neo4j_uri: str, neo4j_user: str, neo4j_password: str, neo4j_database: str):
    """Yields {"type": "progress", "index", "total", "result"} per question, then {"type": "done", "run": ...}."""
    questions = load_questions()
    settings = load_settings()
    compiled = get_compiled_graph()
    started_at = datetime.now(timezone.utc).isoformat()
    results = []

    with GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password)) as driver:
        config = {"configurable": {"client": client, "driver": driver, "database": neo4j_database}}
        for index, question in enumerate(questions):
            started = time.perf_counter()
            try:
                state = compiled.invoke({"question": question["question"], "recent_history": []}, config)
                result = _score(question, state)
            except Exception as error:  # noqa: BLE001 - one failing question must not stop the run
                result = {"passed": False, "errors": [{"node": "run", "tool": "run", "error": str(error)}], "answer": "",
                          "citations": [], "evidence": [], "facts": [], "terms": [], "cost_usd": 0.0}
            result.update({"id": question["id"], "question": question["question"], "goal": question.get("goal", ""),
                           "ms": round((time.perf_counter() - started) * 1000)})
            results.append(result)
            yield {"type": "progress", "index": index + 1, "total": len(questions), "result": result}

    run = {
        "run_at": started_at,
        "models": settings["models"],
        "passed": sum(1 for r in results if r["passed"]),
        "total": len(results),
        "cost_usd": round(sum(r["cost_usd"] for r in results), 6),
        "ms": sum(r["ms"] for r in results),
        "results": results,
    }
    LAST_RUN_PATH.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    history = _read_json(HISTORY_PATH, [])
    history.append({key: run[key] for key in ("run_at", "models", "passed", "total", "cost_usd", "ms")})
    HISTORY_PATH.write_text(json.dumps(history[-HISTORY_LIMIT:], ensure_ascii=False, indent=2), encoding="utf-8")
    yield {"type": "done", "run": run}
