"""The agent graph and its streaming entry point.

Flow (plan-and-execute with parallel fan-out):

    START -> prepare -> summarize -> planner -+-> answer                                  (small talk)
                                              +-> entry -+-> sources      -+
                                                         +-> causes       -+-> check -+-> answer -> END
                                                         +-> architecture -+          +-> explorer -> answer
                                                         +-> people       -+

Every conditional edge declares its possible targets, so `get_graph()` shows the complete flow.

Conversation memory, per thread: the stored messages, a running summary and how many of the stored messages the
summary covers. `prepare` gives the last `history_turns` turns word for word; `summarize` folds the turns before them
into the summary once they leave that window. A stored message is dropped only after it is in the summary, so nothing
is lost between the window and the summary.
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

STORED_HISTORY_MESSAGES = 24  # above the largest recent window (history_turns at most 10, so 20 messages)
MAX_THREADS = 200

# Conversation memory per thread, outside the graph so every question starts from a clean state:
# {"messages": [...], "summary": str, "summarized": int}
_threads: "OrderedDict[str, dict]" = OrderedDict()


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
    graph.add_node("summarize", nodes.summarize)
    graph.add_node("planner", nodes.planner)
    graph.add_node("entry", nodes.entry)
    for name in SPECIALISTS:
        graph.add_node(name, nodes.SPECIALIST_NODES[name])
    graph.add_node("check", nodes.check)
    graph.add_node("explorer", nodes.explorer)
    graph.add_node("answer", nodes.answer)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "summarize")
    graph.add_edge("summarize", "planner")
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


def _remember(thread_id: str, question: str, answer: str, final_state: dict) -> None:
    thread = _threads.pop(thread_id, {"messages": [], "summary": "", "summarized": 0})
    messages = thread["messages"] + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
    summarized = final_state.get("summarized_messages", thread["summarized"])
    summary = final_state.get("conversation_summary", thread["summary"])

    # Drop the oldest messages above the cap, but only those already in the summary (unless the summary is off).
    summary_on = (final_state.get("settings") or {}).get("conversation_summary", {}).get("enabled", True)
    excess = max(len(messages) - STORED_HISTORY_MESSAGES, 0)
    drop = excess if not summary_on else min(excess, summarized)
    drop -= drop % 2  # whole turns
    _threads[thread_id] = {"messages": messages[drop:], "summary": summary, "summarized": max(summarized - drop, 0)}
    while len(_threads) > MAX_THREADS:
        _threads.popitem(last=False)


def stream_chat(message: str, thread_id: str, neo4j_uri: str, neo4j_user: str, neo4j_password: str, neo4j_database: str):
    """Yields event dicts: status, token, tool_error, sources, and finally done (with usage, cost and trace)."""
    client = require_openai_client()
    compiled = get_compiled_graph()

    with GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password)) as driver:
        config = {"configurable": {"client": client, "driver": driver, "database": neo4j_database}}
        thread = _threads.get(thread_id) or {"messages": [], "summary": "", "summarized": 0}
        input_state = {
            "question": message,
            "stored_history": list(thread["messages"]),
            "conversation_summary": thread["summary"],
            "summarized_messages": thread["summarized"],
        }

        final_state: dict = {}
        for mode, chunk in compiled.stream(input_state, config, stream_mode=["custom", "values"]):
            if mode == "custom":
                yield chunk
            else:
                final_state = chunk

    answer = final_state.get("final_answer", "")
    _remember(thread_id, message, answer, final_state)
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
