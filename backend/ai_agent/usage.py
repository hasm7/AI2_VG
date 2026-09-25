"""Token and cost bookkeeping. Every model call adds one entry to the state's `usage` list."""


def _price(settings: dict, model: str) -> dict:
    return settings["prices_usd_per_million_tokens"].get(model, {})


def cost_usd(settings: dict, model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    price = _price(settings, model)
    uncached = max(input_tokens - cached_tokens, 0)
    total = (
        uncached * price.get("input", 0.0)
        + cached_tokens * price.get("cached_input", price.get("input", 0.0))
        + output_tokens * price.get("output", 0.0)
    ) / 1_000_000
    return round(total, 6)


def response_usage(settings: dict, node: str, model: str, response) -> dict:
    """Usage entry for an OpenAI Responses API call."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = getattr(details, "cached_tokens", 0) or 0
    return {
        "node": node,
        "model": model,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd(settings, model, input_tokens, cached_tokens, output_tokens),
        "priced": bool(_price(settings, model)),
    }


def embedding_usage(settings: dict, node: str, model: str, response) -> dict:
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    return {
        "node": node,
        "model": model,
        "input_tokens": input_tokens,
        "cached_tokens": 0,
        "output_tokens": 0,
        "cost_usd": cost_usd(settings, model, input_tokens, 0, 0),
        "priced": bool(_price(settings, model)),
    }


def total_cost(usage: list[dict]) -> float:
    return round(sum(entry.get("cost_usd", 0.0) for entry in usage), 6)


def token_usage_by_node(usage: list[dict]) -> dict:
    """The `{node: {input_tokens, output_tokens}}` shape the chat panel already understands."""
    by_node: dict[str, dict] = {}
    for entry in usage:
        node = by_node.setdefault(entry["node"], {"input_tokens": 0, "output_tokens": 0})
        node["input_tokens"] += entry["input_tokens"]
        node["output_tokens"] += entry["output_tokens"]
    return by_node
