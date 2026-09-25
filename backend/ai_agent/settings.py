"""Agent settings: models per node, budget, specialists on/off, search and evidence sizes, token prices.

Read from `settings.json` at the start of every question, so an edit applies to the next question without a
backend restart. Missing keys fall back to `DEFAULTS`.
"""

import copy
import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).with_name("settings.json")

SPECIALISTS = ("sources", "causes", "architecture", "people")

DEFAULTS = {
    "models": {"planner": "gpt-4o", "specialists": "gpt-4o", "explorer": "gpt-4o", "answer": "gpt-4o"},
    "history_turns": 3,
    "budget_usd_per_question": 0.05,
    "specialists": {name: True for name in SPECIALISTS},
    "specialist_followup": {"enabled": True, "max_rounds": 1, "max_calls_per_round": 2, "max_extra_nodes": 4},
    "explorer": {"enabled": True, "max_queries": 3, "max_rows": 25, "max_result_chars": 4000, "max_extra_nodes": 6},
    "search": {"vector_k": 10, "fulltext_k": 10, "lookup_k": 3, "entry_points": 8},
    "evidence": {"max_nodes_per_specialist": 8, "max_text_chars": 1200},
    "query_timeout_seconds": 10,
    "prices_usd_per_million_tokens": {},
}


def _merge(defaults: dict, overrides: dict) -> dict:
    merged = copy.deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings() -> dict:
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        stored = {}
    return _merge(DEFAULTS, stored)


def enabled_specialists(settings: dict) -> list[str]:
    return [name for name in SPECIALISTS if settings["specialists"].get(name, True)]
