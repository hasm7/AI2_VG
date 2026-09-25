"""Describes the agent graph for the `Configure AI agent` tab.

Nodes and edges come from the compiled graph itself (`get_graph()`), so the drawing always matches what runs. State
fields and their merge rule come from `AgentState`. What each node does, and which fields it reads and writes, is
described here; keep `NODE_INFO` in step with `nodes.py`.
"""

import typing

from .settings import available_models, load_settings
from .state import AgentState

START, END = "__start__", "__end__"

NODE_INFO = {
    START: {"kind": "start", "title": "Start", "description": "The question arrives with the recent conversation.",
            "reads": [], "writes": ["question", "recent_history"]},
    "prepare": {"kind": "code", "title": "Prepare",
                "description": "Loads settings.json, computes which layers are stale, trims the conversation history.",
                "reads": ["question", "recent_history"], "writes": ["settings", "staleness", "recent_history"]},
    "planner": {"kind": "model", "model_key": "planner", "title": "Planner",
                "description": "One model call with structured output: question types, language, entities, English "
                               "keywords, and which specialists to run. Small talk goes straight to the answer.",
                "reads": ["question", "recent_history", "settings"], "writes": ["plan", "usage"]},
    "entry": {"kind": "code", "title": "Entry",
              "description": "Finds the entry points: exact lookup of keys and names, vector search on "
                             "searchable_embedding, fulltext on searchable_text, merged by reciprocal rank fusion. "
                             "One embedding call for the question. Then starts the chosen specialists in parallel.",
              "reads": ["question", "plan", "settings", "usage"], "writes": ["entry_points", "usage", "errors"]},
    "sources": {"kind": "code + model", "model_key": "specialists", "title": "Sources specialist",
                "description": "Source records around the entry points: versions, comments, reviews, code changes, "
                               "mentions, event evidence. Follow-up tools: get_timeline, get_conversation, search_sources.",
                "reads": ["question", "plan", "entry_points", "settings", "usage"], "writes": ["evidence", "usage", "errors"]},
    "causes": {"kind": "code + model", "model_key": "specialists", "title": "Causes specialist",
               "description": "Events, causal links and root causes around the entry points. Follow-up tools: "
                              "get_causal_chain, get_root_causes, search_causes.",
               "reads": ["question", "plan", "entry_points", "settings", "usage"], "writes": ["evidence", "usage", "errors"]},
    "architecture": {"kind": "code + model", "model_key": "specialists", "title": "Architecture specialist",
                     "description": "Components, dependencies and affected components around the entry points. "
                                    "Follow-up tools: get_component, get_file_history, search_architecture.",
                     "reads": ["question", "plan", "entry_points", "settings", "usage"], "writes": ["evidence", "usage", "errors"]},
    "people": {"kind": "code + model", "model_key": "specialists", "title": "People specialist",
               "description": "Persons, expertise, collaboration and communities; metric rankings for ranking "
                              "questions. Follow-up tools: get_person, get_experts, rank.",
               "reads": ["question", "plan", "entry_points", "settings", "usage"], "writes": ["evidence", "usage", "errors"]},
    "check": {"kind": "code", "title": "Check",
              "description": "Is the evidence enough? No entry points, no evidence, a why question without causal "
                             "evidence, or a ranking question without metrics means no.",
              "reads": ["plan", "entry_points", "evidence", "explorer_ran", "usage", "settings"], "writes": ["sufficiency"]},
    "explorer": {"kind": "model", "model_key": "explorer", "title": "Explorer",
                 "description": "Fallback: a model writes read-only Cypher (checked by cypher_guard, read transaction, "
                                "timeout), a few queries at most. Runs at most once, only when the check says no and "
                                "the budget allows.",
                 "reads": ["question", "evidence", "sufficiency", "settings"],
                 "writes": ["evidence", "usage", "explorer_ran"]},
    "answer": {"kind": "model", "model_key": "answer", "title": "Answer",
               "description": "One streamed model call that answers from the evidence only, with citations, in the "
                              "question's language, mentioning stale layers. Refuses in code when a fetch failed.",
               "reads": ["question", "recent_history", "plan", "staleness", "evidence", "errors", "settings"],
               "writes": ["final_answer", "citations", "dropped_citations", "usage"]},
    END: {"kind": "end", "title": "End", "description": "The answer, citations, usage and trace are returned.",
          "reads": ["final_answer", "citations", "dropped_citations", "usage", "trace"], "writes": []},
}

# Every node appends one entry to `trace` (the `traced` wrapper in `nodes.py`).
for _node, _info in NODE_INFO.items():
    if _info["kind"] not in ("start", "end"):
        _info["writes"].append("trace")

EDGE_LABELS = {
    ("planner", "answer"): "small talk",
    ("planner", "entry"): "question about the graph",
    ("entry", "sources"): "Send (parallel)",
    ("entry", "causes"): "Send (parallel)",
    ("entry", "architecture"): "Send (parallel)",
    ("entry", "people"): "Send (parallel)",
    ("entry", "check"): "no entry points",
    ("check", "explorer"): "not enough evidence (once, within budget)",
    ("check", "answer"): "enough evidence",
}

STATE_DESCRIPTIONS = {
    "question": "The user's question.",
    "recent_history": "The last turns of the conversation (history_turns).",
    "settings": "settings.json as read for this question.",
    "staleness": "Per layer: stale or not, and why.",
    "plan": "question_types, language, entities, keywords_en, specialists, route, reason.",
    "entry_points": "Nodes the question enters the graph through, with score and how they were found.",
    "evidence": "One packet per specialist (and the explorer): nodes with their texts, facts, follow-up log.",
    "sufficiency": "Whether the evidence was judged enough, and why.",
    "explorer_ran": "Whether the explorer has run (it runs at most once).",
    "final_answer": "The answer text.",
    "citations": "References in the answer that resolve to evidence nodes.",
    "dropped_citations": "References in the answer that do not resolve.",
    "usage": "One entry per model call: model, tokens, cached tokens, cost.",
    "trace": "One entry per node run: time and a summary.",
    "errors": "Failed fetches; any error makes the answer refuse to state facts.",
}


def _type_name(annotation) -> str:
    if typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    origin = typing.get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation))
    arguments = ", ".join(_type_name(argument) for argument in typing.get_args(annotation))
    return f"{origin.__name__}[{arguments}]"


def _state_fields() -> list[dict]:
    fields = []
    for name, annotation in typing.get_type_hints(AgentState, include_extras=True).items():
        appended = typing.get_origin(annotation) is typing.Annotated
        fields.append({
            "name": name,
            "type": _type_name(annotation),
            "merge": "appended" if appended else "overwrite",
            "description": STATE_DESCRIPTIONS.get(name, ""),
            "written_by": [node for node, info in NODE_INFO.items() if name in info["writes"]],
            "read_by": [node for node, info in NODE_INFO.items() if name in info["reads"]],
        })
    return fields


def describe_agent(compiled_graph) -> dict:
    settings = load_settings()
    drawable = compiled_graph.get_graph()
    nodes = []
    for node_id in drawable.nodes:
        info = NODE_INFO.get(node_id, {"kind": "code", "title": node_id, "description": "", "reads": [], "writes": []})
        model = settings["models"].get(info["model_key"]) if info.get("model_key") else None
        enabled = True
        if node_id in settings["specialists"]:
            enabled = bool(settings["specialists"][node_id])
        elif node_id == "explorer":
            enabled = bool(settings["explorer"]["enabled"])
        nodes.append({
            "id": node_id, "title": info["title"], "kind": info["kind"], "model": model, "enabled": enabled,
            "description": info["description"], "reads": info["reads"], "writes": info["writes"],
        })
    edges = [
        {"source": edge.source, "target": edge.target, "conditional": bool(edge.conditional),
         "label": EDGE_LABELS.get((edge.source, edge.target), "")}
        for edge in drawable.edges
    ]
    return {
        "nodes": nodes, "edges": edges, "state": _state_fields(), "settings": settings,
        "available_models": available_models(settings),
    }
