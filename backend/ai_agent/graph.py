"""The agent graph and its streaming entry point.

Flow (plan-and-execute with parallel fan-out):

    START -> prepare -> planner -+-> answer                                  (small talk)
                                 +-> entry -+-> sources      -+
                                            +-> causes       -+-> check -+-> answer -> END
                                            +-> architecture -+          +-> explorer -> answer
                                            +-> people       -+

Every conditional edge declares its possible targets, so `get_graph()` shows the complete flow.
"""

import os
from collections import OrderedDict

from langgraph.graph import END, START, StateGraph
from neo4j import GraphDatabase
from openai import OpenAI

from . import nodes
from .settings import SPECIALISTS
from .state import AgentState
from .usage import token_usage_by_node, total_cost

STORED_HISTORY_MESSAGES = 20
MAX_THREADS = 200

# Conversation history per thread, outside the graph so every question starts from a clean state.
_histories: "OrderedDict[str, list[dict]]" = OrderedDict()


class MissingApiKeyError(RuntimeError):
    pass


def require_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise MissingApiKeyError("OPENAI_API_KEY is not configured. Add it to .env to use the AI chat.")
    return OpenAI(api_key=api_key)


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("prepare", nodes.prepare)
    graph.add_node("planner", nodes.planner)
    graph.add_node("entry", nodes.entry)
    for name in SPECIALISTS:
        graph.add_node(name, nodes.SPECIALIST_NODES[name])
    graph.add_node("check", nodes.check)
    graph.add_node("explorer", nodes.explorer)
    graph.add_node("answer", nodes.answer)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "planner")
    graph.add_conditional_edges("planner", nodes.route_after_planner, ["answer", "entry"])
    graph.add_conditional_edges("entry", nodes.dispatch_specialists, [*SPECIALISTS, "check"])
    for name in SPECIALISTS:
        graph.add_edge(name, "check")
    graph.add_conditional_edges("check", nodes.route_after_check, ["explorer", "answer"])
    graph.add_edge("explorer", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def _remember(thread_id: str, question: str, answer: str) -> None:
    history = _histories.pop(thread_id, [])
    history = (history + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}])
    _histories[thread_id] = history[-STORED_HISTORY_MESSAGES:]
    while len(_histories) > MAX_THREADS:
        _histories.popitem(last=False)


def stream_chat(message: str, thread_id: str, neo4j_uri: str, neo4j_user: str, neo4j_password: str, neo4j_database: str):
    """Yields event dicts: status, token, tool_error, sources, and finally done (with usage, cost and trace)."""
    client = require_openai_client()
    compiled = get_compiled_graph()

    with GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password)) as driver:
        config = {"configurable": {"client": client, "driver": driver, "database": neo4j_database}}
        input_state = {"question": message, "recent_history": list(_histories.get(thread_id, []))}

        final_state: dict = {}
        for mode, chunk in compiled.stream(input_state, config, stream_mode=["custom", "values"]):
            if mode == "custom":
                yield chunk
            else:
                final_state = chunk

    answer = final_state.get("final_answer", "")
    _remember(thread_id, message, answer)
    usage = final_state.get("usage", [])
    yield {
        "type": "done",
        "answer": answer,
        "citations": final_state.get("citations", []),
        "dropped_citations": final_state.get("dropped_citations", []),
        "token_usage": token_usage_by_node(usage),
        "usage": usage,
        "cost_usd": total_cost(usage),
        "plan": final_state.get("plan"),
        "entry_points": final_state.get("entry_points", []),
        "sufficiency": final_state.get("sufficiency"),
        "trace": final_state.get("trace", []),
    }
