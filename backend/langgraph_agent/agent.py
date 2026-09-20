"""Graph RAG agent: LangGraph StateGraph, supervisor pattern.

Replaces the old single-node passthrough chat. See
`WORK_ORDER_GRAPH_RAG_AGENT.md` for the full design. This module owns graph
construction and the streaming entry point (`stream_chat`); node bodies live
in `nodes.py`, tool schemas/executors in `tools.py`, prompts in
`prompts.py`.

Flow: orchestrator -> (direct -> END) | (search_agent -> graph_agent ->
synthesis -> END) | (graph_agent -> synthesis -> END). `graph_agent` may
route back to `search_agent` once, via `Command`, if the entry nodes it was
given turned out unusable — see `nodes.graph_agent_node`.
"""

import os

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from neo4j import GraphDatabase
from openai import OpenAI

from .nodes import HISTORY_TURNS, direct_node, graph_agent_node, orchestrator_node, search_agent_node, synthesis_node
from .state import ChatState

STORED_HISTORY_LIMIT = HISTORY_TURNS * 4  # keep some slack beyond what any single prompt actually uses


class MissingApiKeyError(RuntimeError):
    pass


def require_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise MissingApiKeyError("OPENAI_API_KEY is not configured. Add it to .env to use the AI chat.")
    return OpenAI(api_key=api_key)


def build_graph():
    graph = StateGraph(ChatState)
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("direct", direct_node)
    graph.add_node("search_agent", search_agent_node)
    graph.add_node("graph_agent", graph_agent_node)
    graph.add_node("synthesis", synthesis_node)

    graph.add_edge(START, "orchestrator")
    # orchestrator routes to "direct" | "search_agent" | "graph_agent", and
    # graph_agent may route back to "search_agent" once — both via Command
    # returned from the node itself, so no static/conditional edges for
    # those are declared here.
    graph.add_edge("search_agent", "graph_agent")
    graph.add_edge("direct", END)
    graph.add_edge("synthesis", END)

    return graph.compile(checkpointer=InMemorySaver())


_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def stream_chat(message: str, thread_id: str, neo4j_uri: str, neo4j_user: str, neo4j_password: str, neo4j_database: str):
    """Yields event dicts: {"type": "status"|"token"|"sources"|"done", ...}.

    Opens one Neo4j read session for the whole turn and hands it to every
    node through `config["configurable"]`, so state itself never carries
    live connections (state must stay checkpointer-friendly).
    """
    client = require_openai_client()
    compiled = get_compiled_graph()

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    session = driver.session(database=neo4j_database, default_access_mode="READ")

    try:
        config = {"configurable": {"thread_id": thread_id, "session": session, "client": client}}
        input_state = {"message": message}
        # `history` is deliberately omitted from the input: the checkpointer
        # merges input over the last checkpoint for this thread_id, so an
        # existing `history` value carries forward automatically. See the
        # module docstring in `state.py` for the field's shape.

        final_state = {}
        for stream_mode, chunk in compiled.stream(input_state, config, stream_mode=["custom", "values"]):
            if stream_mode == "custom":
                yield chunk
            else:
                final_state = chunk

        answer = final_state.get("answer", "")
        citations = final_state.get("citations", [])
        dropped_citations = final_state.get("dropped_citations", [])
        token_usage = final_state.get("token_usage", {})

        history = final_state.get("history", [])
        new_history = (history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ])[-STORED_HISTORY_LIMIT:]
        compiled.update_state(config, {"history": new_history})

        yield {
            "type": "done", "answer": answer, "citations": citations,
            "dropped_citations": dropped_citations, "token_usage": token_usage,
        }
    finally:
        session.close()
        driver.close()
