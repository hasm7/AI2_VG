"""Evaluation of Microsoft GraphRAG on the app's own 27 test questions, next to the app's own agent.

The questions are the agent's (`ai_agent/test_questions.json`), unchanged, so both systems answer exactly the same
questions. Only one part of the agent's scoring can be applied to both: the expected terms in the answer
(`expected_terms`, the same rule as `ai_agent/evaluation.py`: a group passes when any of its variants is in the
answer, case-insensitive). The agent's other checks (route, evidence nodes, facts) name nodes of the app's own graph,
which Microsoft GraphRAG does not have. Scoring is done in code, no model judges the answers.

Microsoft GraphRAG is asked through the GraphRAG service, one question at a time, in a background thread; every
question has the service's cost limit. The agent is not run again: its latest run (`ai_agent/evaluation_last.json`,
from Configure AI agent) is read and scored with the same terms rule. Results are kept per method in
`graphrag_evaluation_last.json`, so a method that was not run again keeps its earlier results.
"""

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUESTIONS_PATH = HERE / "ai_agent" / "test_questions.json"
AGENT_LAST_RUN_PATH = HERE / "ai_agent" / "evaluation_last.json"
RESULTS_PATH = HERE / "graphrag_evaluation_last.json"

METHODS = ["local", "global", "drift", "basic"]
# Measured cost of one question per method with gpt-6-luna (Query tab, 2026-10-08), for the estimate before a run.
COST_PER_QUESTION_USD = {"basic": 0.0006, "local": 0.0013, "drift": 0.008, "global": 0.033}


class EvaluationRunningError(RuntimeError):
    pass


def load_questions() -> list[dict]:
    return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def score_terms(question: dict, answer: str) -> dict:
    """The agent's terms rule (`ai_agent/evaluation.py`): every group must have a variant in the answer."""
    groups = [
        {"expected": group, "found": any(variant.lower() in (answer or "").lower() for variant in group)}
        for group in question.get("expected_terms", [])
    ]
    return {"terms": groups, "terms_ok": all(group["found"] for group in groups)}


# The run started by the backend (kept in memory while the backend runs).
_lock = threading.Lock()
_run: dict = {"running": False}


def start(methods: list[str], ask) -> dict:
    """Starts a run of the chosen methods. `ask(method, question)` returns (payload, status code) from the service."""
    chosen = [method for method in METHODS if method in methods]
    if not chosen:
        raise ValueError("Choose at least one search method.")
    if not _lock.acquire(blocking=False):
        raise EvaluationRunningError("An evaluation is already running.")
    questions = load_questions()
    _run.clear()
    _run.update({"running": True, "started_at": datetime.now(timezone.utc).isoformat(), "methods": chosen,
                 "done": 0, "total": len(questions) * len(chosen), "current": None})
    threading.Thread(target=_run_all, args=(questions, chosen, ask), daemon=True).start()
    return state()


def _run_all(questions: list[dict], methods: list[str], ask) -> None:
    try:
        stored = _read_json(RESULTS_PATH, {"methods": {}})
        for method in methods:
            results = []
            for question in questions:
                _run["current"] = f"{method}: {question['id']}"
                started = time.perf_counter()
                payload, status_code = ask(method, question["question"])
                seconds = round(time.perf_counter() - started, 1)
                answer = payload.get("answer", "") if status_code == 200 else ""
                error = None if status_code == 200 else payload.get("error", f"HTTP {status_code}")
                scored = score_terms(question, answer)
                usage = payload.get("usage") or {}
                results.append({
                    "id": question["id"],
                    "answer": answer,
                    "error": error,
                    **scored,
                    "passed": error is None and scored["terms_ok"],
                    "cost_usd": usage.get("cost_usd", 0.0),
                    "model_calls": usage.get("model_calls", 0),
                    "seconds": seconds,
                })
                _run["done"] += 1
            stored["methods"][method] = {
                "run_at": datetime.now(timezone.utc).isoformat(),
                "passed": sum(1 for result in results if result["passed"]),
                "total": len(results),
                "cost_usd": round(sum(result["cost_usd"] for result in results), 6),
                "results": results,
            }
            RESULTS_PATH.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:  # noqa: BLE001 - reported in the state
        _run["error"] = str(error)
    finally:
        _run["running"] = False
        _run["current"] = None
        _run["finished_at"] = datetime.now(timezone.utc).isoformat()
        _lock.release()


def _agent_results(questions: list[dict]) -> dict | None:
    """The agent's latest run, scored with the same terms rule (its full pass is kept beside it)."""
    run = _read_json(AGENT_LAST_RUN_PATH, None)
    if not run:
        return None
    by_id = {result["id"]: result for result in run.get("results", [])}
    results = []
    for question in questions:
        result = by_id.get(question["id"])
        if result is None:
            continue
        scored = score_terms(question, result.get("answer", ""))
        results.append({
            "id": question["id"],
            "answer": result.get("answer", ""),
            "error": None if not result.get("errors") else "; ".join(str(e.get("error", e)) for e in result["errors"]),
            **scored,
            "passed": scored["terms_ok"] and not result.get("errors"),
            "full_passed": bool(result.get("passed")),
            "cost_usd": result.get("cost_usd", 0.0),
            "model_calls": result.get("model_calls", 0),
            "seconds": round(result.get("ms", 0) / 1000, 1),
        })
    return {
        "run_at": run.get("run_at"),
        "passed": sum(1 for result in results if result["passed"]),
        "full_passed": sum(1 for result in results if result["full_passed"]),
        "total": len(results),
        "cost_usd": round(sum(result["cost_usd"] for result in results), 6),
        "results": results,
    }


def state() -> dict:
    questions = load_questions()
    return {
        "questions": [{"id": q["id"], "question": q["question"], "goal": q.get("goal", ""),
                       "expected_terms": q.get("expected_terms", [])} for q in questions],
        "methods": METHODS,
        "estimate_per_question_usd": COST_PER_QUESTION_USD,
        "run": {key: value for key, value in _run.items()},
        "graphrag": _read_json(RESULTS_PATH, {"methods": {}})["methods"],
        "agent": _agent_results(questions),
    }
