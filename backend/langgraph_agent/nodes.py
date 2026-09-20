"""Node implementations for the graph RAG agent's StateGraph.

Every node is `(state, config) -> dict | Command`. Live dependencies
(Neo4j session, OpenAI client) come through `config["configurable"]`, never
through state, so state stays a plain, checkpointer-friendly structure. Each
node that does work emits a `status` event through LangGraph's custom
stream writer before it starts, and `synthesis`/`direct` additionally emit
`token` events as the model streams — the only two nodes whose output
reaches the user (`WORK_ORDER_GRAPH_RAG_AGENT.md` section 6).
"""

import json
import re
from typing import Callable, Literal, Optional

from langgraph.config import get_stream_writer
from langgraph.types import Command
from pydantic import BaseModel, Field

from .prompts import (
    DIRECT_SYSTEM_PROMPT,
    GRAPH_SYSTEM_PROMPT,
    ORCHESTRATOR_SYSTEM_PROMPT,
    SEARCH_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
)
from .state import ChatState
from .tools import graph_agent_tools, search_agent_tools, strip_raw_blob_properties

# The one tool exempt from the shared raw-blob projection funnel in
# `run_tool_loop` — it exists specifically to return the untrimmed node.
_UNTRIMMED_TOOL_NAME = "get_full_node"

ORCHESTRATOR_MODEL = "gpt-5.6-terra"
SEARCH_MODEL = "gpt-5.6-terra"
GRAPH_MODEL = "gpt-5.6-sol"
SYNTHESIS_MODEL = "gpt-5.6-terra"

MAX_TOOL_ITERATIONS = 4
HISTORY_TURNS = 10

# Once accumulated tool-result JSON in one specialist's loop crosses this,
# no further tool calls are offered and the model is forced to answer with
# whatever it already has. Item 1 (the dense schema reference) removes most
# of the graph_agent cost; this is the second, independent guard against a
# tool loop that just keeps fetching more (`REMEDIATION_ORDER...` item 3).
TOOL_OUTPUT_CHAR_BUDGET = 40_000

_KEY_FIELDS = [
    "key", "display_name", "issue_key", "slug", "neighbour_key",
    "person_key", "message_id", "comment_id", "segment_id",
]

# Identifier shapes actually produced by this graph: AUTH-17, AUTH-17 v3,
# mail-002, doc-001 v2, seg-003, comment-004, backend-api#42,
# backend-api#42 review-005. Used only to flag citation-shaped substrings
# the model wrote that do not resolve against the bundle, for visibility
# during testing (see `find_dropped_citations`), not to build citations.
_CITATION_SHAPE_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Z0-9]*-\d+(?:\s+v\d+)?"
    r"|[a-z]+-\d+(?:\s+v\d+)?"
    r"|[a-z0-9][a-z0-9._-]*#\d+(?:\s+(?:v\d+|review-\d+))?"
    r")\b"
)


class RouteOut(BaseModel):
    route: Literal["direct", "search", "graph"]
    reason: str


BUNDLE_NODE_LIMIT = 15


class BundleNodeOut(BaseModel):
    label: str
    key: str
    display_name: str
    text: str = ""
    relations: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None


class ContextBundleOut(BaseModel):
    nodes: list[BundleNodeOut]


def _format_history(history: list[dict]) -> str:
    if not history:
        return ""
    recent = history[-HISTORY_TURNS:]
    lines = [f"{turn['role']}: {turn['content']}" for turn in recent]
    return "Recent conversation:\n" + "\n".join(lines) + "\n\n"


def _usage_dict(response) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }


def _merge_usage(existing: dict | None, role: str, usage: dict) -> dict:
    merged = dict(existing or {})
    role_usage = dict(merged.get(role, {"input_tokens": 0, "output_tokens": 0}))
    role_usage["input_tokens"] = role_usage.get("input_tokens", 0) + usage.get("input_tokens", 0)
    role_usage["output_tokens"] = role_usage.get("output_tokens", 0) + usage.get("output_tokens", 0)
    merged[role] = role_usage
    return merged


def _sanitize_output_item(item) -> dict:
    """Strips server-populated fields the Responses API's `input` side
    rejects when a `response.output` item is echoed straight back
    (observed: `id`, `status`, `async_`/`async`, `caller`, `namespace` all
    trigger `Unknown parameter` errors on different item types). For a
    `function_call`, keep only the fields the tool-call loop actually needs.
    """
    dumped = item.model_dump()
    if dumped.get("type") == "function_call":
        return {
            "type": "function_call",
            "call_id": dumped.get("call_id"),
            "name": dumped.get("name"),
            "arguments": dumped.get("arguments"),
        }
    for key in ("id", "status", "async_", "async", "caller", "namespace"):
        dumped.pop(key, None)
    return dumped


def run_tool_loop(
    client, model: str, instructions: str, input_items: list[dict],
    tools: list[dict], executors: dict[str, Callable], node_name: str,
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> tuple[str, list[dict], dict, list[dict], bool, list[dict]]:
    """Drives the OpenAI Responses tool-call loop until the model stops calling tools.

    Returns (final_text, tool_calls_log, usage_totals, tool_errors,
    budget_exceeded, input_items). `tool_calls_log` holds every {"tool",
    "args", "result"} regardless of what the model says afterwards — the
    structured results are what downstream nodes build their context
    bundle and citation metadata from, not the model's prose summary of
    them. `tool_errors` is every result that came back with an `"error"`
    key — a tool failing is reported, never silently worked around
    (`REMEDIATION_ORDER_GRAPH_RAG_AGENT.md` item 2), and each one is also
    emitted immediately as a `tool_error` stream event as it happens. The
    returned `input_items` is the final accumulated conversation, handed
    back so a caller (`graph_agent_node`) can extend it for one more call
    — e.g. the bundle-assembly call — without losing the cached prefix.
    """
    writer = get_stream_writer()
    tool_calls_log: list[dict] = []
    tool_errors: list[dict] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    output_chars = 0
    budget_exceeded = False

    for _ in range(max_iterations):
        response = client.responses.create(
            model=model, instructions=instructions, input=input_items, tools=tools,
        )
        response_usage = _usage_dict(response)
        usage_total["input_tokens"] += response_usage["input_tokens"]
        usage_total["output_tokens"] += response_usage["output_tokens"]

        function_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if not function_calls:
            return response.output_text, tool_calls_log, usage_total, tool_errors, budget_exceeded, input_items

        # `response.output` items carry server-populated fields (`id`,
        # `status`, ...) that the API's `input` side rejects when echoed
        # straight back (`Unknown parameter: 'input[N].status'`). Strip
        # those before feeding the turn back in for the next call.
        input_items = input_items + [_sanitize_output_item(item) for item in response.output]
        for call in function_calls:
            try:
                args = json.loads(call.arguments) if call.arguments else {}
            except json.JSONDecodeError:
                args = {}

            executor = executors.get(call.name)
            if executor is None:
                result = {"error": f"Unknown tool: {call.name}"}
            else:
                try:
                    result = executor(args)
                except Exception as error:  # noqa: BLE001 - reported as a tool error, never re-raised
                    result = {"error": str(error)}

            if call.name != _UNTRIMMED_TOOL_NAME:
                result = strip_raw_blob_properties(result)

            if isinstance(result, dict) and result.get("error"):
                error_entry = {"node": node_name, "tool": call.name, "args": args, "error": result["error"]}
                tool_errors.append(error_entry)
                writer({"type": "tool_error", **error_entry})

            tool_calls_log.append({"tool": call.name, "args": args, "result": result})
            result_json = json.dumps(result, ensure_ascii=False, default=str)
            output_chars += len(result_json)
            input_items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result_json,
            })

        if output_chars >= TOOL_OUTPUT_CHAR_BUDGET:
            budget_exceeded = True
            writer({"type": "status", "text": "Verktygsbudget nådd, svarar med det som redan hämtats..."})
            break

    # Ran out of iterations, or the tool-output budget was hit: force a
    # final answer with no more tools offered, same call shape either way.
    response = client.responses.create(model=model, instructions=instructions, input=input_items)
    response_usage = _usage_dict(response)
    usage_total["input_tokens"] += response_usage["input_tokens"]
    usage_total["output_tokens"] += response_usage["output_tokens"]
    return response.output_text, tool_calls_log, usage_total, tool_errors, budget_exceeded, input_items


def collect_citable_nodes(obj, metadata: dict[str, dict]) -> None:
    """Walks a tool-call result and records every node key it can cite, with enough metadata to render a citation."""
    if isinstance(obj, dict):
        found_key = None
        for field in _KEY_FIELDS:
            value = obj.get(field)
            if isinstance(value, str) and value:
                found_key = value
                break
        if found_key:
            metadata.setdefault(found_key, {
                "label": obj.get("label") or obj.get("neighbour_label") or "",
                "display_name": obj.get("display_name") or found_key,
                "source_url": obj.get("source_url"),
            })
        for value in obj.values():
            collect_citable_nodes(value, metadata)
    elif isinstance(obj, list):
        for item in obj:
            collect_citable_nodes(item, metadata)


def extract_citations(answer_text: str, metadata: dict[str, dict]) -> list[dict]:
    citations = []
    seen = set()
    for key in sorted(metadata.keys(), key=len, reverse=True):
        if key in seen or not key:
            continue
        if key in answer_text:
            meta = metadata[key]
            citations.append({
                "label": meta["label"], "key": key,
                "display_name": meta["display_name"], "source_url": meta["source_url"],
            })
            seen.add(key)
    return citations


def find_dropped_citations(answer_text: str, metadata: dict[str, dict]) -> list[str]:
    dropped = []
    for match in _CITATION_SHAPE_PATTERN.finditer(answer_text):
        candidate = match.group(0)
        if candidate not in metadata and candidate not in dropped:
            dropped.append(candidate)
    return dropped


# ---------------------------------------------------------------------------
# Nodes.
# ---------------------------------------------------------------------------

def orchestrator_node(state: ChatState, config) -> Command:
    writer = get_stream_writer()
    writer({"type": "status", "text": "Dirigerar frågan..."})

    client = config["configurable"]["client"]
    prompt_input = _format_history(state.get("history", [])) + f"Current question: {state['message']}"

    response = client.responses.parse(
        model=ORCHESTRATOR_MODEL, instructions=ORCHESTRATOR_SYSTEM_PROMPT,
        input=prompt_input, text_format=RouteOut,
    )
    parsed = response.output_parsed
    token_usage = _merge_usage(state.get("token_usage"), "orchestrator", _usage_dict(response))

    # `retry_count` and `tool_errors` are reset here, not carried over from
    # a previous turn — otherwise a failure two questions ago would still
    # be blocking synthesis on an unrelated question today.
    update = {
        "route": parsed.route, "route_reason": parsed.reason,
        "retry_count": 0, "tool_errors": [], "token_usage": token_usage,
    }
    next_node = {"direct": "direct", "search": "search_agent", "graph": "graph_agent"}[parsed.route]
    return Command(update=update, goto=next_node)


def direct_node(state: ChatState, config) -> dict:
    writer = get_stream_writer()
    client = config["configurable"]["client"]
    prompt_input = _format_history(state.get("history", [])) + f"Current question: {state['message']}"

    full_text = []
    with client.responses.stream(
        model=SYNTHESIS_MODEL, instructions=DIRECT_SYSTEM_PROMPT, input=prompt_input,
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                writer({"type": "token", "text": event.delta})
                full_text.append(event.delta)
        final_response = stream.get_final_response()

    token_usage = _merge_usage(state.get("token_usage"), "direct", _usage_dict(final_response))
    return {"answer": "".join(full_text), "citations": [], "dropped_citations": [], "token_usage": token_usage}


def search_agent_node(state: ChatState, config) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "text": "Söker i grafen..."})

    session = config["configurable"]["session"]
    client = config["configurable"]["client"]
    schemas, executors = search_agent_tools(session, client)

    retry_note = ""
    if state.get("retry_count", 0) > 0:
        retry_note = (
            "\n\nThe previous entry nodes could not be used for traversal. "
            "Try different tools or a reformulated query."
        )
    prompt_input = f"Question: {state['message']}{retry_note}\n\nFind the best entry nodes into the graph for this question."

    _, tool_calls_log, usage, tool_errors, _budget_exceeded, _input_items = run_tool_loop(
        client, SEARCH_MODEL, SEARCH_SYSTEM_PROMPT, [{"role": "user", "content": prompt_input}],
        schemas, executors, "search_agent",
    )

    metadata: dict[str, dict] = {}
    collect_citable_nodes(tool_calls_log, metadata)
    search_results = [{"key": key, **meta} for key, meta in metadata.items()]

    token_usage = _merge_usage(state.get("token_usage"), "search_agent", usage)
    all_tool_errors = (state.get("tool_errors") or []) + tool_errors
    return {"search_results": search_results, "token_usage": token_usage, "tool_errors": all_tool_errors}


def graph_agent_node(state: ChatState, config) -> Command:
    writer = get_stream_writer()
    writer({"type": "status", "text": "Hämtar sammanhang i grafen..."})

    session = config["configurable"]["session"]
    client = config["configurable"]["client"]
    schemas, executors = graph_agent_tools(session)

    search_results = state.get("search_results") or []
    entry_summary = json.dumps(search_results, ensure_ascii=False) if search_results else "(none — enter by exact key from the question itself)"
    prompt_input = (
        f"Question: {state['message']}\n\n"
        f"Entry nodes found by the search specialist: {entry_summary}\n\n"
        "Gather evidence with your tools for this question (or, if there are no "
        "entry nodes, enter directly by any exact key named in the question). "
        "You will be asked to assemble the context bundle once you are done."
    )

    _, tool_calls_log, usage, tool_errors, budget_exceeded, transcript = run_tool_loop(
        client, GRAPH_MODEL, GRAPH_SYSTEM_PROMPT, [{"role": "user", "content": prompt_input}],
        schemas, executors, "graph_agent",
    )

    # One more call on the same (cached) transcript: turn everything the
    # loop gathered into the bounded, purpose-built bundle that is all
    # `synthesis` will see — not the tool transcript itself
    # (`FINAL_REMEDIATION_ORDER_GRAPH_RAG_AGENT.md` item 1).
    bundle_request = {
        "role": "user",
        "content": (
            f"Assemble the context bundle now, per the contract in your instructions: "
            f"at most {BUNDLE_NODE_LIMIT} node records, the ones most relevant to the question."
        ),
    }
    bundle_response = client.responses.parse(
        model=GRAPH_MODEL, instructions=GRAPH_SYSTEM_PROMPT,
        input=transcript + [bundle_request], text_format=ContextBundleOut,
    )
    bundle_usage = _usage_dict(bundle_response)
    usage["input_tokens"] += bundle_usage["input_tokens"]
    usage["output_tokens"] += bundle_usage["output_tokens"]

    parsed = bundle_response.output_parsed
    raw_nodes = parsed.nodes[:BUNDLE_NODE_LIMIT] if parsed else []

    metadata: dict[str, dict] = {}
    bundle_nodes = []
    for node in raw_nodes:
        text = node.text[:1500] if node.text else ""
        bundle_nodes.append({
            "label": node.label, "key": node.key, "display_name": node.display_name,
            "text": text, "relations": node.relations, "source_url": node.source_url,
        })
        metadata[node.key] = {"label": node.label, "display_name": node.display_name, "source_url": node.source_url}

    context_bundle = {
        "question": state["message"], "nodes": bundle_nodes,
        "tool_errors": tool_errors, "budget_exceeded": budget_exceeded, "metadata": metadata,
    }
    token_usage = _merge_usage(state.get("token_usage"), "graph_agent", usage)
    all_tool_errors = (state.get("tool_errors") or []) + tool_errors
    update = {"context_bundle": context_bundle, "token_usage": token_usage, "tool_errors": all_tool_errors}

    unusable = not bundle_nodes
    can_retry = state.get("route") == "search" and state.get("retry_count", 0) < 1
    if unusable and can_retry:
        writer({"type": "status", "text": "Inga användbara träffar, söker igen..."})
        update["retry_count"] = state.get("retry_count", 0) + 1
        return Command(update=update, goto="search_agent")

    return Command(update=update, goto="synthesis")


def _format_tool_errors(tool_errors: list[dict]) -> str:
    return "\n".join(f"- {error['tool']} ({error['node']}): {error['error']}" for error in tool_errors)


def synthesis_node(state: ChatState, config) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "text": "Skriver svar..."})

    tool_errors = state.get("tool_errors") or []
    if tool_errors:
        # Deterministic, not model-composed: the whole point of this branch
        # is that we do not trust the model to reliably decline to answer
        # on its own when something failed — see the incident this fixes in
        # `REMEDIATION_ORDER_GRAPH_RAG_AGENT.md` item 2. A code-level "no"
        # cannot be talked around the way a prompt instruction can.
        message = (
            f"Jag kunde inte hämta det här ur grafen just nu — {len(tool_errors)} "
            f"verktygsanrop misslyckades:\n{_format_tool_errors(tool_errors)}\n\n"
            "Jag svarar inte med sakuppgifter om grafens innehåll när ett hämtningsförsök "
            "har misslyckats den här omgången, för att inte riskera att gissa fel. Fråga "
            "gärna igen."
        )
        for word in message.split(" "):
            writer({"type": "token", "text": word + " "})
        writer({"type": "sources", "citations": [], "dropped_citations": []})
        return {
            "answer": message, "citations": [], "dropped_citations": [],
            "token_usage": state.get("token_usage") or {},
        }

    client = config["configurable"]["client"]
    bundle = state.get("context_bundle") or {"question": state["message"], "nodes": [], "tool_errors": [], "metadata": {}}
    metadata = bundle.get("metadata", {})
    # `metadata` is this node's own lookup for citation validation
    # (`extract_citations`/`find_dropped_citations` below) — it is not part
    # of the bundle contract and is never sent to the model.
    bundle_for_prompt = {key: value for key, value in bundle.items() if key != "metadata"}
    prompt_input = (
        _format_history(state.get("history", []))
        + f"Current question: {state['message']}\n\n"
        f"Context bundle from the graph specialist:\n{json.dumps(bundle_for_prompt, ensure_ascii=False, default=str)}"
    )

    full_text = []
    with client.responses.stream(
        model=SYNTHESIS_MODEL, instructions=SYNTHESIS_SYSTEM_PROMPT, input=prompt_input,
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                writer({"type": "token", "text": event.delta})
                full_text.append(event.delta)
        final_response = stream.get_final_response()

    answer = "".join(full_text)
    citations = extract_citations(answer, metadata)
    dropped = find_dropped_citations(answer, metadata)

    writer({"type": "sources", "citations": citations, "dropped_citations": dropped})

    token_usage = _merge_usage(state.get("token_usage"), "synthesis", _usage_dict(final_response))
    return {"answer": answer, "citations": citations, "dropped_citations": dropped, "token_usage": token_usage}
