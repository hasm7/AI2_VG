"""Shared state shape for the graph RAG agent's LangGraph StateGraph."""

from typing import Literal, TypedDict


class Citation(TypedDict):
    label: str
    key: str
    display_name: str
    source_url: str | None


class ChatState(TypedDict, total=False):
    thread_id: str
    message: str
    history: list[dict]  # [{"role": "user"|"assistant", "content": str}], last 10 turns

    route: Literal["direct", "search", "graph"]
    route_reason: str
    retry_count: int

    search_results: list[dict]
    context_bundle: dict
    tool_errors: list[dict]  # [{"node": "search_agent"|"graph_agent", "tool": str, "args": dict, "error": str}]

    answer: str
    citations: list[Citation]
    dropped_citations: list[str]

    token_usage: dict[str, int]
