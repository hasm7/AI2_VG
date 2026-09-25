"""Bounded model follow-up for a specialist, and the explorer.

Follow-up: after a specialist's code fetch, one cheap model call sees the question and a compact view of the evidence
(names and a short snippet, not the full texts). If something is clearly missing it calls one of the specialist's own
few tools (fixed, parameterised Cypher for that layer only); otherwise it replies DONE. At most `max_rounds` rounds and
`max_calls_per_round` calls; the new nodes are added to the packet, at most `max_extra_nodes`.

Explorer: the fallback when `check` finds the evidence insufficient. A model writes read-only Cypher (checked by
`cypher_guard` and run in a read transaction with a timeout), at most `max_queries` queries, rows and characters capped.
"""

import json
import re

from cypher_guard import enforce_read_only

from .db import fetch_nodes, node_text, read
from .prompts import EXPLORER_PROMPT, followup_prompt
from .specialists import SOURCE_LABELS
from .usage import response_usage

ELEMENT_ID_PATTERN = re.compile(r"^\d+:[0-9a-f-]{36}:\d+$")
SNIPPET_CHARS = 160

# ---------------------------------------------------------------------------
# Tool queries. Each returns rows with `id` (nodes to add as evidence) or `fact` (a line of text).
# ---------------------------------------------------------------------------

_NAME = "toLower(coalesce(n.display_name, n.name, n.title, n.issue_key, ''))"
RESOLVE = f"""
MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels)
  AND ({_NAME} = toLower($text) OR {_NAME} CONTAINS toLower($text))
RETURN elementId(n) AS id
ORDER BY CASE WHEN {_NAME} = toLower($text) THEN 0 ELSE 1 END, size({_NAME})
LIMIT 3
"""

SEARCH_IN_LABELS = """
CALL db.index.fulltext.queryNodes('searchable_text', $q, {limit: 25}) YIELD node AS n, score
WHERE any(l IN labels(n) WHERE l IN $labels)
RETURN elementId(n) AS id
LIMIT 6
"""

TIMELINE = """
MATCH (p) WHERE elementId(p) IN $ids
CALL (p) {
  MATCH (p)-[:HAS_ISSUE_VERSION|HAS_ISSUE_COMMENT|HAS_DOCUMENT_VERSION|HAS_PR_REVIEW|HAS_CODE_CHANGE]->(c:Searchable)
  RETURN c
  UNION
  MATCH (c:Searchable)-[r:MENTIONS_ISSUE|MENTIONS_PULL_REQUEST|MENTIONS_DOCUMENT]->(p)
  WHERE r.extracted_by = 'reference-extraction-v1'
  RETURN c
}
RETURN DISTINCT elementId(c) AS id, toString(coalesce(c.version_at, c.sent_at, c.created_at)) AS at
ORDER BY at
LIMIT 12
"""

CONVERSATION = """
MATCH (n) WHERE elementId(n) IN $ids
OPTIONAL MATCH (m:TeamsMeeting)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(n)
OPTIONAL MATCH (m)-[:HAS_TEAMS_TRANSCRIPT_SEGMENT]->(segment)
OPTIONAL MATCH (thread:SlackMessage)
  WHERE n:SlackMessage AND (thread.message_id = coalesce(n.thread_root_id, n.message_id)
                            OR thread.thread_root_id = coalesce(n.thread_root_id, n.message_id))
OPTIONAL MATCH (mail:MailMessage)
  WHERE n:MailMessage AND (mail.message_id = n.in_reply_to_id OR mail.in_reply_to_id = n.message_id)
WITH n, collect(DISTINCT segment) + collect(DISTINCT thread) + collect(DISTINCT mail) + [n] AS related
UNWIND related AS r
RETURN DISTINCT elementId(r) AS id
LIMIT 12
"""

CAUSAL_CHAIN = """
MATCH (e:Event) WHERE elementId(e) IN $ids
MATCH (e)-[links*1..3]-(other:Event)
WHERE all(link IN links WHERE type(link) IN ['CAUSED', 'CROSS_TOPIC_CAUSED'])  // as text: no warning while none exist
RETURN DISTINCT elementId(other) AS id
LIMIT 10
"""

ROOT_CAUSES_OF = """
MATCH (s) WHERE elementId(s) IN $ids
CALL (s) {
  MATCH (s:Event)-[:HAS_ROOT_CAUSE]->(r:RootCause) RETURN r
  UNION
  MATCH (r:RootCause)-[:ROOT_CAUSE_IN_COMPONENT]->(s:Component) RETURN r
  UNION
  MATCH (s:Topic)<-[:EVENT_OF_TOPIC]-(:Event)-[:HAS_ROOT_CAUSE]->(r:RootCause) RETURN r
}
RETURN DISTINCT elementId(r) AS id
LIMIT 8
"""

COMPONENT_CONTEXT = """
MATCH (c:Component) WHERE elementId(c) IN $ids
CALL (c) {
  RETURN c AS x
  UNION
  MATCH (c)-[:DEPENDS_ON]-(x:Component) RETURN x
  UNION
  MATCH (c)-[:IMPLEMENTED_IN]->(:File)<-[:MODIFIES_FILE]-(x:CodeChange) RETURN x
}
RETURN DISTINCT elementId(x) AS id
LIMIT 10
"""

FILE_HISTORY = """
MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower($text)
MATCH (f)<-[:MODIFIES_FILE]-(x)
RETURN DISTINCT elementId(x) AS id
UNION
MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower($text)
MATCH (f)<-[:IMPLEMENTED_IN]-(x:Component)
RETURN DISTINCT elementId(x) AS id
"""

PERSON_CONTEXT = """
MATCH (p:Person:Searchable) WHERE elementId(p) IN $ids
CALL (p) {
  RETURN p AS x
  UNION
  MATCH (p)-[:WORKS_WITH]-(x:Person:Searchable) RETURN x
}
RETURN DISTINCT elementId(x) AS id
LIMIT 8
"""

EXPERTS_ON = """
MATCH (s) WHERE elementId(s) IN $ids
MATCH (p:Person)-[:HAS_EXPERTISE]->(x:Expertise)-[:EXPERTISE_IN]->(s)
RETURN p.name + ' on ' + coalesce(s.name, s.display_name) + ': share ' + toString(x.share) + ', rank '
       + toString(x.rank) + ', score ' + toString(x.score) AS fact
ORDER BY x.rank
LIMIT 10
"""

RANKINGS = {
    "betweenness": """
        MATCH (p:Person:Searchable) WHERE p.collab_betweenness IS NOT NULL
        RETURN p.name + ': betweenness ' + toString(p.collab_betweenness) AS fact
        ORDER BY p.collab_betweenness DESC""",
    "weighted_degree": """
        MATCH (p:Person:Searchable) WHERE p.collab_weighted_degree IS NOT NULL
        RETURN p.name + ': weighted degree ' + toString(p.collab_weighted_degree) AS fact
        ORDER BY p.collab_weighted_degree DESC""",
    "bus_factor": """
        MATCH (s) WHERE (s:Topic OR s:Component) AND s.bus_factor IS NOT NULL
        RETURN s.name + ': bus factor ' + toString(s.bus_factor) + ', top expert ' + coalesce(s.top_expert, '-') AS fact
        ORDER BY s.bus_factor ASC""",
}


def _tool(name: str, description: str, argument: str, argument_description: str, enum: list[str] | None = None) -> dict:
    schema = {"type": "string", "description": argument_description}
    if enum:
        schema["enum"] = enum
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": {argument: schema}, "required": [argument], "additionalProperties": False},
        "strict": True,
    }


def _resolve(session, text: str, labels: list[str], timeout: float) -> list[str]:
    if not text.strip():
        return []
    return [row["id"] for row in read(session, RESOLVE, {"text": text, "labels": labels}, timeout)]


def _ids(rows: list[dict]) -> dict:
    return {"ids": [row["id"] for row in rows], "facts": []}


def _facts(rows: list[dict]) -> dict:
    return {"ids": [], "facts": [row["fact"] for row in rows]}


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# name -> (schema, executor(session, argument, timeout) -> {"ids", "facts"})
SPECIALIST_TOOLS = {
    "sources": {
        "get_timeline": (
            _tool("get_timeline", "All versions, comments, reviews, code changes and mentioning messages of one issue, "
                  "document or pull request, in time order.", "reference", "Issue key, pull request or document id/title."),
            lambda s, a, t: _ids(read(s, TIMELINE, {"ids": _resolve(s, a, ["Issue", "Document", "PullRequest"], t)}, t)),
        ),
        "get_conversation": (
            _tool("get_conversation", "The surrounding conversation of one message: its Slack thread, its mail reply "
                  "chain, or the whole meeting transcript of a segment.", "reference", "Message or segment id, e.g. slack-007."),
            lambda s, a, t: _ids(read(s, CONVERSATION, {"ids": _resolve(s, a, ["SlackMessage", "MailMessage", "TeamsTranscriptSegment"], t)}, t)),
        ),
        "search_sources": (
            _tool("search_sources", "Exact-word search in source records only.", "query", "English words or identifiers."),
            lambda s, a, t: _ids(read(s, SEARCH_IN_LABELS, {"q": _quote(a), "labels": SOURCE_LABELS}, t)),
        ),
    },
    "causes": {
        "get_causal_chain": (
            _tool("get_causal_chain", "Events up to three causal steps before or after one event.", "event", "Event name."),
            lambda s, a, t: _ids(read(s, CAUSAL_CHAIN, {"ids": _resolve(s, a, ["Event"], t)}, t)),
        ),
        "get_root_causes": (
            _tool("get_root_causes", "Root causes behind an event, located in a component, or behind a topic's events.",
                  "subject", "Event, component or topic name."),
            lambda s, a, t: _ids(read(s, ROOT_CAUSES_OF, {"ids": _resolve(s, a, ["Event", "Component", "Topic"], t)}, t)),
        ),
        "search_causes": (
            _tool("search_causes", "Exact-word search in events, root causes and topics only.", "query", "English words."),
            lambda s, a, t: _ids(read(s, SEARCH_IN_LABELS, {"q": _quote(a), "labels": ["Event", "RootCause", "Topic"]}, t)),
        ),
    },
    "architecture": {
        "get_component": (
            _tool("get_component", "One component with the components it depends on or is used by, and the code "
                  "changes to its files.", "component", "Component name."),
            lambda s, a, t: _ids(read(s, COMPONENT_CONTEXT, {"ids": _resolve(s, a, ["Component"], t)}, t)),
        ),
        "get_file_history": (
            _tool("get_file_history", "Code changes to a file and the components implemented in it.", "path",
                  "File path or part of it, e.g. mobile_refresh.py."),
            lambda s, a, t: _ids(read(s, FILE_HISTORY, {"text": a}, t)),
        ),
        "search_architecture": (
            _tool("search_architecture", "Exact-word search in components and code changes only.", "query", "English words."),
            lambda s, a, t: _ids(read(s, SEARCH_IN_LABELS, {"q": _quote(a), "labels": ["Component", "CodeChange"]}, t)),
        ),
    },
    "people": {
        "get_person": (
            _tool("get_person", "One person's profile and the people they work with.", "name", "Person name."),
            lambda s, a, t: _ids(read(s, PERSON_CONTEXT, {"ids": _resolve(s, a, ["Person"], t)}, t)),
        ),
        "get_experts": (
            _tool("get_experts", "Expertise shares and ranks of every person on one topic or component.", "subject",
                  "Topic or component name."),
            lambda s, a, t: _facts(read(s, EXPERTS_ON, {"ids": _resolve(s, a, ["Topic", "Component"], t)}, t)),
        ),
        "rank": (
            _tool("rank", "A full ranking by one metric.", "metric", "The metric to rank by.",
                  enum=["betweenness", "weighted_degree", "bus_factor"]),
            lambda s, a, t: _facts(read(s, RANKINGS[a], None, t)) if a in RANKINGS else {"ids": [], "facts": []},
        ),
    },
}


def compact_evidence(nodes: list[dict], facts: list[str]) -> str:
    """Names and a one-line snippet per node: enough to judge what is missing, far cheaper than the full texts."""
    lines = []
    for node in nodes:
        snippet = " ".join((node.get("text") or "").split())[:SNIPPET_CHARS]
        lines.append(f"- {node['label']} {node['name']}: {snippet}")
    lines.extend(f"- fact: {fact[:SNIPPET_CHARS]}" for fact in facts)
    return "\n".join(lines) or "(nothing yet)"


def _function_calls(response) -> list:
    return [item for item in response.output if getattr(item, "type", None) == "function_call"]


def _echo(call) -> dict:
    return {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments}


def run_followup(name: str, session, client, question: str, packet: dict, settings: dict) -> list[dict]:
    """Extends `packet` in place. Returns the usage entries of the model calls made."""
    config = settings["specialist_followup"]
    timeout = settings["query_timeout_seconds"]
    model = settings["models"]["specialists"]
    tools = SPECIALIST_TOOLS[name]
    schemas = [schema for schema, _ in tools.values()]

    usage: list[dict] = []
    tool_log: list[str] = []
    new_ids: list[str] = []
    input_items: list[dict] = [{
        "role": "user",
        "content": f"Question: {question}\n\nEvidence already fetched:\n{compact_evidence(packet['nodes'], packet['facts'])}",
    }]

    for round_index in range(config["max_rounds"]):
        response = client.responses.create(
            model=model, instructions=followup_prompt(name), input=input_items, tools=schemas, parallel_tool_calls=True,
        )
        usage.append(response_usage(settings, name, model, response))
        calls = _function_calls(response)[: config["max_calls_per_round"]]
        if not calls:
            break
        for call in calls:
            try:
                argument = next(iter(json.loads(call.arguments or "{}").values()), "")
                result = tools[call.name][1](session, str(argument), timeout) if call.name in tools else {"ids": [], "facts": []}
            except Exception as error:  # noqa: BLE001 - a failed follow-up only means no extra evidence
                result = {"ids": [], "facts": [], "error": str(error)}
            new_ids.extend(i for i in result["ids"] if i not in new_ids)
            packet["facts"].extend(result["facts"])
            tool_log.append(f"{call.name}({argument}) -> {len(result['ids'])} nodes, {len(result['facts'])} facts"
                            + (f", error: {result['error']}" if result.get("error") else ""))
            if round_index + 1 < config["max_rounds"]:
                input_items += [_echo(call), {"type": "function_call_output", "call_id": call.call_id,
                                              "output": json.dumps({"added_nodes": len(result["ids"]), "facts": result["facts"][:10]})}]

    known = {node["id"] for node in packet["nodes"]}
    extra_ids = [i for i in new_ids if i not in known][: config["max_extra_nodes"]]
    rows = fetch_nodes(session, extra_ids, timeout)
    for node_id in extra_ids:
        if node_id in rows:
            row = rows[node_id]
            packet["nodes"].append({"id": node_id, "label": row["label"], "name": row["name"], "at": row.get("at"),
                                    "priority": 3, "text": node_text(row, settings["evidence"]["max_text_chars"])})
    packet["followup"] = tool_log or ["no follow-up needed"]
    return usage


def run_explorer(session, client, question: str, evidence: list[dict], reason: str, settings: dict) -> tuple[dict, list[dict]]:
    """Returns (evidence packet, usage entries)."""
    config = settings["explorer"]
    timeout = settings["query_timeout_seconds"]
    model = settings["models"]["explorer"]
    tool = {
        "type": "function",
        "name": "run_cypher",
        "description": "Runs one read-only Cypher query. Return elementId(n) AS id for nodes you want as evidence.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                       "additionalProperties": False},
        "strict": True,
    }
    known_nodes = [node for packet in evidence for node in packet["nodes"]]
    known_facts = [fact for packet in evidence for fact in packet["facts"]]
    input_items: list[dict] = [{
        "role": "user",
        "content": (f"Question: {question}\nWhy the evidence was judged insufficient: {reason}\n\n"
                    f"Evidence already fetched:\n{compact_evidence(known_nodes, known_facts)}"),
    }]

    usage: list[dict] = []
    ids: list[str] = []
    facts: list[str] = []
    log: list[str] = []
    for _ in range(config["max_queries"]):
        response = client.responses.create(model=model, instructions=EXPLORER_PROMPT, input=input_items, tools=[tool])
        usage.append(response_usage(settings, "explorer", model, response))
        calls = _function_calls(response)[:1]
        if not calls:
            break
        call = calls[0]
        query = json.loads(call.arguments or "{}").get("query", "")
        try:
            rows = read(session, enforce_read_only(query, config["max_rows"]), None, timeout)[: config["max_rows"]]
            output = json.dumps(rows, ensure_ascii=False, default=str)[: config["max_result_chars"]]
            for row in rows:
                for value in row.values():
                    if isinstance(value, str) and ELEMENT_ID_PATTERN.match(value) and value not in ids:
                        ids.append(value)
            if rows:
                facts.append(f"Query result ({len(rows)} rows): {output}")
            log.append(f"{' '.join(query.split())[:300]} -> {len(rows)} rows")
        except Exception as error:  # noqa: BLE001 - a rejected write or a bad query: the model sees the error and may retry
            output = json.dumps({"error": str(error)})
            log.append(f"{' '.join(query.split())[:300]} -> error: {str(error)[:200]}")
        input_items += [_echo(call), {"type": "function_call_output", "call_id": call.call_id, "output": output}]

    known = {node["id"] for node in known_nodes}
    extra_ids = [i for i in ids if i not in known][: config["max_extra_nodes"]]
    rows_by_id = fetch_nodes(session, extra_ids, timeout)
    nodes = [
        {"id": i, "label": rows_by_id[i]["label"], "name": rows_by_id[i]["name"], "at": rows_by_id[i].get("at"),
         "priority": 3, "text": node_text(rows_by_id[i], settings["evidence"]["max_text_chars"])}
        for i in extra_ids if i in rows_by_id
    ]
    total_chars, kept_facts = 0, []
    for fact in facts:
        if total_chars + len(fact) > config["max_result_chars"]:
            break
        kept_facts.append(fact)
        total_chars += len(fact)
    packet = {"specialist": "explorer", "nodes": nodes, "facts": kept_facts, "candidates": len(ids), "errors": [],
              "followup": log or ["no query made"]}
    return packet, usage
