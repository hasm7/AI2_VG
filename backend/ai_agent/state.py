"""State of one question as it flows through the agent graph.

Fields marked "appended" use `operator.add`, so parallel specialists add to them instead of overwriting each other.
The state lives for one question only; conversation history is kept outside the graph (see `graph.py`).
"""

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    # The conversation, kept per thread in `graph.py` and passed in with every question.
    stored_history: list[dict]  # [{"role": "user"|"assistant", "content": str}], the stored messages of the thread
    conversation_summary: str  # running summary of the messages before `recent_history`
    summarized_messages: int  # how many of `stored_history`'s first messages the summary covers
    # Set by `prepare`.
    recent_history: list[dict]  # the last `history_turns` turns of `stored_history`, given word for word
    pending_history: list[dict]  # messages that left the recent window and are not in the summary yet

    settings: dict
    staleness: dict  # {stage: {"stale": bool, "reasons": [str]}}

    plan: dict  # standalone_question, question_types, language, entities, keywords_en, specialists, route, reason
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
