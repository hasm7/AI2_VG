# Work Order: Embeddings — Implementation Report

Implements `WORK_ORDER_EMBEDDINGS.md` in full. Every decision in that document was followed as specified; nothing was substituted.

## Files changed

| File | Change |
| --- | --- |
| `backend/embedding_pass.py` | New. Third pass: text assembly per label, incremental hash-based embedding, vector + fulltext index setup, `run_embedding_pass`/`build_embeddings`, `embedding_state_payload`. |
| `backend/app.py` | Added `GET /api/embeddings`, `POST /api/embeddings/build` (`{"force": bool}`). `MissingApiKeyError` → `503`, matching the existing `/api/ai/chat` contract rather than the `/api/knowledge/build` 500-instead-of-503 bug noted in `KNOWLEDGE_LAYER_INFO_RESPONSE.md` §1 — not repeated here. |
| `frontend/src/main.tsx` | New `EmbeddingPanel` component; `BuildGraphLayersPanel`'s tab type widened to `"step1" \| "step2" \| "step3"`, third "Embeddings" tab added. |
| `frontend/src/styles.css` | `.embedding-secondary-button`, `.embedding-confirm`, `.embedding-confirm-button`, `.embedding-failures` — everything else reuses the existing `reference-*`/`knowledge-*` classes, per the work order's own "mirror the existing structure" instruction. |

`requirements.txt` unchanged — `openai>=1.99` already covers the embeddings endpoint; `hashlib` is stdlib.

## Verification (work order §5, all 8 steps run against the live database)

Ran with explicit confirmation, since this writes indexes and ~60 embedding properties to Neo4j and makes real (cheap) OpenAI embedding calls.

1. **`SHOW INDEXES`: 10 `VECTOR` + 2 `FULLTEXT`, all `ONLINE`.** Confirmed.
2. **Every node of the ten labels has all four stamps.** Confirmed — first run embedded 61/61 eligible nodes (4 mail, 12 slack, 9 transcript segments, 7 issue versions, 5 issue comments, 3 document versions, 6 PR reviews, 2 PRs, 1 topic, 12 events), 0 failures, 0 empty-text skips.
3. **A second run embeds zero nodes, panel says so explicitly.** Confirmed — `embedded: 0, skipped: 61`. The UI's `justRan` branch renders "0 nodes embedded — everything is up to date." for exactly this case.
4. **"Re-embed all" (`force: true`) embeds every node regardless of stamps.** Confirmed — 61/61 re-embedded.
5. **Knowledge-layer rebuild → embedding run embeds only `Topic`/`Event`.** Confirmed — rebuild produced 1 topic / 11 events (a different event count than the previous session's 11 or 12, consistent with the non-determinism already established in `KNOWLEDGE_LAYER_INFO_RESPONSE.md` §5 — expected, not a defect here); the following embedding run embedded exactly `Topic: 1, Event: 11` and skipped all 48 source-layer nodes as unchanged.
6. **Re-import → embedding run embeds zero nodes.** Confirmed by calling `viewer/app.py`'s `import_mail_to_neo4j` directly (re-importing all 4 mail rows, unchanged source data) and then running the embedding pass: `embedded: 0` for `MailMessage`, all 4 skipped as unchanged. The `MERGE ... SET <explicit field list>` pattern does not touch `embedding`/`embedding_model`/`embedding_source_hash`/`embedded_at`, exactly as predicted.
7. **Vector query returns sensible hits.** `CALL db.index.vector.queryNodes('issueversion_embedding', 5, $qv)` for `"administrator session expiry policy"` returned all 5 `AUTH-17` versions, ranked by similarity, ahead of everything else in the graph (top score 0.82).
8. **Fulltext query returns the issue.** `CALL db.index.fulltext.queryNodes('issue_key_lookup', 'AUTH-17')` returned `AUTH-17` (score 0.40) and `AUTH-19` (score 0.08, lower — a partial/fuzzy match, not a false positive).

## One note, not a defect

Neo4j `2026.08.1` emits a deprecation warning on `db.index.vector.queryNodes`, pointing at a replacement `SEARCH` clause syntax. The procedure still works and was used in the verification query above exactly as the work order's §5 specifies it. Worth knowing before wiring this into the retrieval agent later, since that's explicitly out of scope here — the query shape in the future agent work may want to use `SEARCH` instead once its exact syntax on this version is confirmed.

## Net state of the database after this work order

10 vector indexes, 2 fulltext indexes, and embeddings on all 61 eligible nodes across the ten labels, all under `text-embedding-3-large` / 1536 dimensions / cosine. `PipelineState.last_embedding_at` is set. This is a live, queryable index today — not just code that compiles.
