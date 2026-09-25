"""State of one question as it flows through the agent graph.

Fields marked "appended" use `operator.add`, so parallel specialists add to them instead of overwriting each other.
The state lives for one question only; conversation history is kept outside the graph (see `graph.py`).
"""

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    recent_history: list[dict]  # [{"role": "user"|"assistant", "content": str}], at most `history_turns` turns

    settings: dict
    staleness: dict  # {stage: {"stale": bool, "reasons": [str]}}

    plan: dict  # question_types, language, entities, keywords_en, specialists, route, reason
    entry_points: list[dict]  # [{"id", "label", "name", "score", "via"}]

    evidence: Annotated[list[dict], operator.add]  # appended: one packet per specialist (and the explorer)
    sufficiency: dict  # {"ok": bool, "reason": str}
    explorer_ran: bool

    final_answer: str
    citations: list[dict]
    dropped_citations: list[str]

    usage: Annotated[list[dict], operator.add]  # appended: one entry per model call
    trace: Annotated[list[dict], operator.add]  # appended: one entry per node run
    errors: Annotated[list[dict], operator.add]  # appended: tool and query errors
