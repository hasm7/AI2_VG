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


def available_models(settings: dict) -> list[str]:
    """Chat models that can be chosen per step: those with a price, so their cost can be counted."""
    return sorted(model for model in settings["prices_usd_per_million_tokens"] if not model.startswith("text-embedding"))


class SettingsError(ValueError):
    pass


# Settings that the Configure AI agent tab may change: (group, key) -> (type, minimum, maximum). Models are checked
# against `available_models`; prices are edited in the file only.
EDITABLE_NUMBERS = {
    (None, "history_turns"): (int, 0, 10),
    (None, "budget_usd_per_question"): (float, 0.001, 1.0),
    (None, "query_timeout_seconds"): (int, 1, 60),
    ("specialist_followup", "max_rounds"): (int, 1, 3),
    ("specialist_followup", "max_calls_per_round"): (int, 1, 3),
    ("specialist_followup", "max_extra_nodes"): (int, 0, 10),
    ("explorer", "max_queries"): (int, 1, 5),
    ("explorer", "max_rows"): (int, 5, 100),
    ("explorer", "max_result_chars"): (int, 1000, 10000),
    ("explorer", "max_extra_nodes"): (int, 0, 10),
    ("search", "vector_k"): (int, 1, 50),
    ("search", "fulltext_k"): (int, 1, 50),
    ("search", "lookup_k"): (int, 1, 10),
    ("search", "entry_points"): (int, 1, 20),
    ("evidence", "max_nodes_per_specialist"): (int, 1, 20),
    ("evidence", "max_text_chars"): (int, 200, 4000),
}
EDITABLE_FLAGS = {("specialist_followup", "enabled"), ("explorer", "enabled")}


def save_settings(update: dict) -> dict:
    """Validates a partial update, merges it into the stored settings and writes settings.json. Raises SettingsError."""
    current = load_settings()
    changed = copy.deepcopy(current)
    models = available_models(current)

    for step, model in (update.get("models") or {}).items():
        if step not in current["models"]:
            raise SettingsError(f"Unknown model step: {step}.")
        if model not in models:
            raise SettingsError(f"Model {model} has no price in settings.json; choose one of {', '.join(models)}.")
        changed["models"][step] = model

    for name, value in (update.get("specialists") or {}).items():
        if name not in SPECIALISTS or not isinstance(value, bool):
            raise SettingsError(f"Invalid specialist setting: {name}.")
        changed["specialists"][name] = value

    editable_groups = {group for group, _ in EDITABLE_NUMBERS if group} | {group for group, _ in EDITABLE_FLAGS}
    for key, value in update.items():
        if key in ("models", "specialists"):
            continue
        if isinstance(value, dict):
            if key not in editable_groups:
                raise SettingsError(f"{key} cannot be changed here.")
            for inner_key, inner_value in value.items():
                _apply(changed, key, inner_key, inner_value)
        else:
            _apply(changed, None, key, value)

    stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}
    stored = _merge(stored, {key: changed[key] for key in changed if key != "prices_usd_per_million_tokens"})
    SETTINGS_PATH.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return load_settings()


def _apply(settings: dict, group: str | None, key: str, value) -> None:
    target = settings if group is None else settings.get(group)
    if (group, key) in EDITABLE_FLAGS:
        if not isinstance(value, bool):
            raise SettingsError(f"{group}.{key} must be true or false.")
        target[key] = value
        return
    if (group, key) not in EDITABLE_NUMBERS:
        raise SettingsError(f"{(group + '.') if group else ''}{key} cannot be changed here.")
    kind, low, high = EDITABLE_NUMBERS[(group, key)]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and float(value) != int(value)):
        raise SettingsError(f"{(group + '.') if group else ''}{key} must be a {'whole number' if kind is int else 'number'}.")
    if not low <= value <= high:
        raise SettingsError(f"{(group + '.') if group else ''}{key} must be between {low} and {high}.")
    target[key] = kind(value)
