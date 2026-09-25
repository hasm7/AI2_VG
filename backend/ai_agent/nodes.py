"""Node bodies. Every node is `(state, config) -> dict`. Live dependencies (OpenAI client, Neo4j driver, database
name) come through `config["configurable"]`, never through state.

`traced` wraps every node: it emits a status event, times the node and appends one `trace` entry.
"""

import functools
import re
import time
from typing import Literal

from langgraph.config import get_stream_writer
from langgraph.types import Send
from pydantic import BaseModel, Field

from pipeline_staleness import STAGE_LABELS, compute_staleness, read_pipeline_state

from .followup import run_explorer, run_followup
from .prompts import ANSWER_PROMPT, PLANNER_PROMPT
from .retrieval import find_entry_points
from .settings import SPECIALISTS, enabled_specialists, load_settings
from .specialists import run_specialist
from .usage import response_usage, total_cost

QuestionType = Literal["smalltalk", "lookup", "why", "ranking", "who", "timeline", "impact", "other"]
SpecialistName = Literal["sources", "causes", "architecture", "people"]

_BRACKET_PATTERN = re.compile(r"\[([^\[\]]+)\]")


class PlanOut(BaseModel):
    question_types: list[QuestionType]
    language: str
    entities: list[str] = Field(default_factory=list)
    keywords_en: list[str] = Field(default_factory=list)
    specialists: list[SpecialistName] = Field(default_factory=list)
    reason: str


def traced(node_name: str, status_text: str):
    def decorate(body):
        @functools.wraps(body)
        def node(state, config):
            get_stream_writer()({"type": "status", "text": status_text})
            started = time.perf_counter()
            update = body(state, config) or {}
            summary = update.pop("_summary", "")
            update["trace"] = [{"node": node_name, "ms": round((time.perf_counter() - started) * 1000), "summary": summary}]
            return update
        return node
    return decorate


def _format_history(history: list[dict]) -> str:
    if not history:
        return ""
    return "Recent conversation:\n" + "\n".join(f"{turn['role']}: {turn['content']}" for turn in history) + "\n\n"


def _error(node: str, tool: str, message: str) -> dict:
    entry = {"node": node, "tool": tool, "args": {}, "error": message}
    get_stream_writer()({"type": "tool_error", **entry})
    return entry


# ---------------------------------------------------------------------------
# prepare (code)
# ---------------------------------------------------------------------------

@traced("prepare", "Förbereder...")
def prepare(state, config):
    settings = load_settings()
    driver = config["configurable"]["driver"]
    with driver.session(database=config["configurable"]["database"], default_access_mode="READ") as session:
        pipeline_state = session.execute_read(read_pipeline_state)
    staleness = compute_staleness(pipeline_state)
    history = (state.get("recent_history") or [])[-settings["history_turns"] * 2:]
    stale = [STAGE_LABELS[stage] for stage, value in staleness.items() if value["stale"]]
    return {
        "settings": settings,
        "staleness": staleness,
        "recent_history": history,
        "_summary": f"stale layers: {', '.join(stale) or 'none'}; history messages: {len(history)}",
    }


# ---------------------------------------------------------------------------
# planner (one model call, structured output)
# ---------------------------------------------------------------------------

@traced("planner", "Planerar...")
def planner(state, config):
    settings = state["settings"]
    client = config["configurable"]["client"]
    model = settings["models"]["planner"]

    response = client.responses.parse(
        model=model,
        instructions=PLANNER_PROMPT,
        input=_format_history(state.get("recent_history", [])) + f"Question: {state['question']}",
        text_format=PlanOut,
    )
    parsed: PlanOut = response.output_parsed
    enabled = enabled_specialists(settings)

    route = "smalltalk" if parsed.question_types == ["smalltalk"] else "graph"
    specialists = [name for name in dict.fromkeys(parsed.specialists) if name in enabled]
    if route == "graph" and not specialists:
        # The planner named no usable specialist: ask every enabled one rather than miss the answer.
        specialists = enabled

    plan = {**parsed.model_dump(), "specialists": specialists, "route": route}
    return {
        "plan": plan,
        "usage": [response_usage(settings, "planner", model, response)],
        "_summary": f"route {route}; types {', '.join(parsed.question_types)}; specialists {', '.join(specialists) or '-'}",
    }


def route_after_planner(state) -> str:
    return "answer" if state["plan"]["route"] == "smalltalk" else "entry"


# ---------------------------------------------------------------------------
# entry (code: lookup + hybrid search)
# ---------------------------------------------------------------------------

@traced("entry", "Söker ingångar i grafen...")
def entry(state, config):
    settings = state["settings"]
    configurable = config["configurable"]
    try:
        with configurable["driver"].session(database=configurable["database"], default_access_mode="READ") as session:
            entry_points, usage, details = find_entry_points(
                session, configurable["client"], state["question"], state["plan"], settings,
            )
    except Exception as error:  # noqa: BLE001 - reported, and the answer step refuses to guess
        return {"entry_points": [], "errors": [_error("entry", "hybrid_search", str(error))], "_summary": "failed"}
    return {
        "entry_points": entry_points,
        "usage": usage,
        "_summary": (
            f"{len(entry_points)} entry points (lookup {details['lookup_hits']}, vector {details['vector_hits']}, "
            f"fulltext {details['fulltext_hits']})"
        ),
    }


def dispatch_specialists(state):
    """Fan-out: one `Send` per chosen specialist; they run in parallel. Straight to `check` when there is nothing to do."""
    if not state.get("entry_points") or not state["plan"]["specialists"]:
        return "check"
    payload = {
        "question": state["question"],
        "plan": state["plan"],
        "entry_points": state["entry_points"],
        "settings": state["settings"],
        # Parallel specialists cannot see each other's spending; each checks the budget as it stood at the fan-out.
        "usage": list(state.get("usage") or []),
    }
    return [Send(name, payload) for name in state["plan"]["specialists"]]


def _within_budget(state) -> bool:
    return total_cost(state.get("usage") or []) < state["settings"]["budget_usd_per_question"]


# ---------------------------------------------------------------------------
# specialists (code; one node per specialist)
# ---------------------------------------------------------------------------

def make_specialist(name: str):
    @traced(name, f"Specialist {name} hämtar...")
    def specialist(state, config):
        configurable = config["configurable"]
        settings = state["settings"]
        followup = None
        if settings["specialist_followup"]["enabled"] and _within_budget(state):
            def followup(session, packet):
                return run_followup(name, session, configurable["client"], state["question"], packet, settings)

        packet, usage = run_specialist(
            name, configurable["driver"], configurable["database"],
            [point["id"] for point in state["entry_points"]], state["plan"], settings, followup,
        )
        errors = [_error(name, name, message) for message in packet["errors"]]
        followup_note = "; follow-up: " + " | ".join(packet.get("followup", ["off"]))
        return {
            "evidence": [packet],
            "errors": errors,
            "usage": usage,
            "_summary": (f"{len(packet['nodes'])} nodes ({packet['candidates']} candidates from the code fetch); "
                         f"{len(packet['facts'])} facts{followup_note}"),
        }
    specialist.__name__ = name
    return specialist


SPECIALIST_NODES = {name: make_specialist(name) for name in SPECIALISTS}


# ---------------------------------------------------------------------------
# check (code) and explorer (placeholder in step 1)
# ---------------------------------------------------------------------------

@traced("check", "Kontrollerar underlaget...")
def check(state, config):
    evidence = state.get("evidence") or []
    types = state["plan"]["question_types"]
    by_specialist = {packet["specialist"]: packet for packet in evidence}
    amount = sum(len(packet["nodes"]) + len(packet["facts"]) for packet in evidence)

    if not state.get("entry_points"):
        ok, reason = False, "no entry points found"
    elif amount == 0:
        ok, reason = False, "no evidence found"
    elif "why" in types and "causes" in by_specialist and not by_specialist["causes"]["nodes"]:
        ok, reason = False, "why question without causal evidence"
    elif "ranking" in types and not any(packet["facts"] for packet in evidence):
        ok, reason = False, "ranking question without metrics"
    else:
        ok, reason = True, f"{amount} evidence items"
    return {"sufficiency": {"ok": ok, "reason": reason}, "_summary": ("ok: " if ok else "not enough: ") + reason}


def route_after_check(state) -> str:
    enabled = state["settings"]["explorer"]["enabled"]
    if enabled and not state["sufficiency"]["ok"] and not state.get("explorer_ran") and _within_budget(state):
        return "explorer"
    return "answer"


@traced("explorer", "Utforskar grafen...")
def explorer(state, config):
    configurable = config["configurable"]
    try:
        with configurable["driver"].session(database=configurable["database"], default_access_mode="READ") as session:
            packet, usage = run_explorer(
                session, configurable["client"], state["question"], state.get("evidence") or [],
                state["sufficiency"]["reason"], state["settings"],
            )
    except Exception as error:  # noqa: BLE001 - the explorer is optional; its failure leaves the evidence as it was
        return {"explorer_ran": True, "_summary": f"failed: {error}"}
    return {
        "explorer_ran": True,
        "evidence": [packet],
        "usage": usage,
        "_summary": f"{len(packet['nodes'])} nodes, {len(packet['facts'])} facts; queries: {' | '.join(packet['followup'])}",
    }


# ---------------------------------------------------------------------------
# answer (one streamed model call)
# ---------------------------------------------------------------------------

def _evidence_text(evidence: list[dict]) -> tuple[str, dict[str, dict]]:
    """Evidence as prompt text, and reference -> node metadata for citation checking."""
    lines: list[str] = []
    references: dict[str, dict] = {}
    for packet in evidence:
        if not packet["nodes"] and not packet["facts"]:
            continue
        lines.append(f"### {packet['specialist']}")
        for node in packet["nodes"]:
            reference = node["name"]
            references.setdefault(reference, {"label": node["label"], "display_name": node["name"]})
            when = f"; time: {node['at']}" if node.get("at") else ""
            lines.append(f"[{reference}]\ntype: {node['label']}{when}\n{node['text']}\n")
        for fact in packet["facts"]:
            lines.append(f"Fact: {fact}\n")
    return "\n".join(lines), references


def _citations(answer: str, references: dict[str, dict]) -> tuple[list[dict], list[str]]:
    citations, dropped, seen = [], [], set()
    for match in _BRACKET_PATTERN.finditer(answer):
        for candidate in (part.strip() for part in match.group(1).split(";")):
            if candidate in seen or not candidate:
                continue
            seen.add(candidate)
            if candidate in references:
                meta = references[candidate]
                citations.append({"label": meta["label"], "key": candidate, "display_name": meta["display_name"], "source_url": None})
            else:
                dropped.append(candidate)
    return citations, dropped


def _stream_words(writer, text: str) -> None:
    for word in text.split(" "):
        writer({"type": "token", "text": word + " "})


@traced("answer", "Skriver svar...")
def answer(state, config):
    writer = get_stream_writer()
    settings = state["settings"]

    errors = state.get("errors") or []
    if errors:
        # Decided in code, not in the prompt: when a fetch failed, no facts are stated, so nothing is guessed.
        message = (
            f"Jag kunde inte hämta allt ur grafen just nu ({len(errors)} fel):\n"
            + "\n".join(f"- {e['node']}: {e['error']}" for e in errors)
            + "\n\nJag svarar inte med sakuppgifter när en hämtning har misslyckats, för att inte gissa. Fråga gärna igen."
        )
        _stream_words(writer, message)
        writer({"type": "sources", "citations": [], "dropped_citations": []})
        return {"final_answer": message, "citations": [], "dropped_citations": [], "_summary": "refused: fetch errors"}

    evidence_text, references = _evidence_text(state.get("evidence") or [])
    stale = [STAGE_LABELS[stage] for stage, value in (state.get("staleness") or {}).items() if value["stale"]]
    prompt_input = (
        _format_history(state.get("recent_history", []))
        + f"Question: {state['question']}\n"
        + f"Answer language: {state['plan'].get('language') or 'the language of the question'}\n\n"
        + f"Stale layers: {', '.join(stale) or 'none'}\n\n"
        + ("Evidence:\n" + evidence_text if evidence_text else "Evidence: none.")
    )

    client = config["configurable"]["client"]
    model = settings["models"]["answer"]
    parts: list[str] = []
    with client.responses.stream(model=model, instructions=ANSWER_PROMPT, input=prompt_input) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                writer({"type": "token", "text": event.delta})
                parts.append(event.delta)
        final_response = stream.get_final_response()

    text = "".join(parts)
    citations, dropped = _citations(text, references)
    writer({"type": "sources", "citations": citations, "dropped_citations": dropped})
    return {
        "final_answer": text,
        "citations": citations,
        "dropped_citations": dropped,
        "usage": [response_usage(settings, "answer", model, final_response)],
        "_summary": f"{len(citations)} citations, {len(dropped)} unresolved references",
    }
