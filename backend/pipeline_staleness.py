"""Single source of truth for pipeline staleness across every layer.

Each layer used to compare its own `PipelineState` timestamp only against
its direct upstream timestamps. That misses transitive staleness: if
Reference extraction is itself stale, a downstream layer whose direct
upstream is References would still report `false` as long as its own
timestamp happens to be newer than `last_extraction_at`. `compute_staleness`
evaluates every stage in dependency order so that a stale upstream always
propagates downstream, with a human-readable reason attached.

`PIPELINE_STATE_LABEL`/`PIPELINE_STATE_ID` are duplicated from
`reference_extraction.py` rather than imported, so that module (a
foundational dependency of the other layers) does not need to import this
one back.
"""

from datetime import datetime

PIPELINE_STATE_LABEL = "PipelineState"
PIPELINE_STATE_ID = "singleton"

TIMESTAMP_BY_STAGE = {
    "import": "last_import_at",
    "references": "last_extraction_at",
    "knowledge": "last_layer_build_at",
    "architecture": "last_architecture_build_at",
    "causal": "last_causal_build_at",
    "collaboration": "last_collaboration_build_at",
    "algorithms": "last_algorithms_run_at",
    "embeddings": "last_embedding_at",
}

UPSTREAM_BY_STAGE = {
    "references": ["import"],
    "knowledge": ["references"],
    "architecture": ["import", "references"],
    "causal": ["knowledge", "architecture"],
    "collaboration": ["import", "knowledge", "architecture", "causal"],
    "algorithms": ["knowledge", "architecture", "causal", "collaboration"],
    "embeddings": ["import", "references", "knowledge", "architecture", "causal", "collaboration", "algorithms"],
}

STAGE_LABELS = {
    "import": "Import",
    "references": "Reference extraction",
    "knowledge": "Knowledge layer",
    "architecture": "Architecture layer",
    "causal": "Root cause & impact layer",
    "collaboration": "Expertise & collaboration layer",
    "algorithms": "Graph algorithms",
    "embeddings": "Embeddings",
}


def _parse_timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def compute_staleness(pipeline_state: dict) -> dict:
    """Returns {stage: {"stale": bool, "reasons": [str]}} for every stage except "import"."""
    parsed = {stage: _parse_timestamp(pipeline_state.get(field)) for stage, field in TIMESTAMP_BY_STAGE.items()}
    results = {}

    # `UPSTREAM_BY_STAGE` is already written in dependency order: every
    # stage's upstream stages appear as keys earlier in this dict, so a
    # single left-to-right pass can always see `results[upstream]` already
    # computed by the time it is needed.
    for stage, upstream_stages in UPSTREAM_BY_STAGE.items():
        own = parsed[stage]

        if own is None:
            results[stage] = {"stale": True, "reasons": ["Never built."]}
            continue

        reasons = []
        for upstream in upstream_stages:
            upstream_ts = parsed[upstream]
            if upstream_ts is not None and upstream_ts > own:
                reasons.append(f"{STAGE_LABELS[upstream]} was rebuilt after this layer.")
            if upstream != "import" and results[upstream]["stale"]:
                reasons.append(f"{STAGE_LABELS[upstream]} is stale.")

        results[stage] = {"stale": len(reasons) > 0, "reasons": reasons}

    return results


def read_pipeline_state(tx):
    row = tx.run(f"""
        MATCH (s:{PIPELINE_STATE_LABEL} {{id: $id}})
        RETURN s.last_import_at AS last_import_at, s.last_extraction_at AS last_extraction_at,
               s.last_layer_build_at AS last_layer_build_at,
               s.last_architecture_build_at AS last_architecture_build_at,
               s.last_causal_build_at AS last_causal_build_at,
               s.last_collaboration_build_at AS last_collaboration_build_at,
               s.last_algorithms_run_at AS last_algorithms_run_at,
               s.last_embedding_at AS last_embedding_at
    """, {"id": PIPELINE_STATE_ID}).single()
    if not row:
        return {field: None for field in TIMESTAMP_BY_STAGE.values()}
    return dict(row)
