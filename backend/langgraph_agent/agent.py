"""Single-node LangGraph agent backed by OpenAI."""

from __future__ import annotations

import os
from typing import TypedDict

from langgraph.graph import END, StateGraph
from openai import OpenAI


DEFAULT_MODEL = "gpt-4.1-mini"


class AgentState(TypedDict):
    message: str
    answer: str


def call_openai(state: AgentState) -> AgentState:
    """Call OpenAI and return the assistant answer."""
    message = state["message"].strip()
    if not message:
        return {"message": state["message"], "answer": "Write a message first."}

    client = OpenAI()
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        input=[
            {
                "role": "system",
                "content": (
                    "You are a concise assistant inside a software-team graph app. "
                    "Answer in Swedish unless the user asks for another language."
                ),
            },
            {"role": "user", "content": message},
        ],
    )

    return {"message": state["message"], "answer": response.output_text}


def build_agent():
    """Build a one-node LangGraph app."""
    graph = StateGraph(AgentState)
    graph.add_node("openai_agent", call_openai)
    graph.set_entry_point("openai_agent")
    graph.add_edge("openai_agent", END)
    return graph.compile()


agent = build_agent()


def ask_agent(message: str) -> str:
    """Run the LangGraph agent for one user message."""
    result = agent.invoke({"message": message, "answer": ""})
    return result["answer"]
