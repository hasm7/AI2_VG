# Knowledge Layer Build and Embedding Pass Placement — Answers

Answers to `KNOWLEDGE_LAYER_INFO_REQUEST.md`, from reading `frontend/src/main.tsx`, `backend/app.py`, `backend/topic_event_extraction.py`, and `backend/reference_extraction.py` verbatim. No code was changed and nothing was executed against Neo4j or OpenAI for sections 1–4, 6, 7 — they are answerable from static code reading alone.

**Section 5 required actually running the knowledge-layer build twice**, which deletes and rewrites live Neo4j data and makes real paid OpenAI calls (`gpt-5.6-terra`, reasoning effort `medium`, once per `Issue` — 2 calls per run against the current fixture, 4 total for both runs). Per `AGENTS.md`'s rule against modifying the database without explicit confirmation, that experiment was only run after an explicit go-ahead — see section 5 for the results.

---

## 1. The knowledge layer build, end to end

**Frontend.** `frontend/src/main.tsx`, `KnowledgeLayerPanel` component (~line 1277). On mount, `loadState()` does `GET /api/knowledge` and stores the full response in `state`. The button ("Build knowledge layer") calls `runBuild()`:

```tsx
const runBuild = async () => {
  setError("");
  setIsRunning(true);
  setJustRan(false);
  try {
    const response = await fetch("/api/knowledge/build", { method: "POST" });
    const data = (await response.json()) as KnowledgeState;
    if (!response.ok || data.error) {
      throw new Error(data.error || "Build failed.");
    }
    setState(data);
    setJustRan(true);
  } catch (runError) {
    setError(runError instanceof Error ? runError.message : "Build failed.");
  } finally {
    setIsRunning(false);
  }
};
```

No request body is sent. `isRunning` only toggles the button's label between "Build knowledge layer" and "Building knowledge layer..." — there is no partial UI update while the request is in flight. The entire `topics`/`events`/`causal_links` table is replaced in one shot from the single JSON response when the fetch resolves.

**Endpoint.** `backend/app.py`, `POST /api/knowledge/build` (`api_knowledge_build`, lines 412–425):

```python
@app.route("/api/knowledge/build", methods=["POST", "OPTIONS"])
def api_knowledge_build():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        uri, user, password, database = neo4j_connection_settings()
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session(database=database) as session:
                result = build_knowledge_layer(session)
        return jsonify(result)
    except Exception as error:
        return jsonify({"error": str(error)}), 500
```

Runs **synchronously inside the Flask request handler**. The HTTP response is not sent until `build_knowledge_layer` fully returns — no background task, no job queue, no `202 Accepted` + poll pattern. The request stays open for the full duration of every OpenAI call plus every Neo4j write.

**Entry function.** `backend/topic_event_extraction.py`, `build_knowledge_layer(session)` → `run_topic_event_extraction(session, postgres_database_url())` (lines 718–812). Ordered steps:

1. `require_openai_client()` — raises `MissingApiKeyError` if `OPENAI_API_KEY` unset (caught by the Flask route's generic `except Exception`, surfaced as a 500 with the error text — not a 503 as `docs/GRAPH_DATA_HANDOFF.md` currently claims; see note below).
2. `build_registry(database_url)` — reads all of PostgreSQL to build the person-identity registry (same one the SQL→Neo4j import uses).
3. `generated_at = now_iso()`.
4. `ensure_knowledge_constraints` (write) — `CREATE CONSTRAINT ... IF NOT EXISTS` for `topic_slug` and `event_key`.
5. `delete_generated_relationships` (write) — see section 2.
6. `delete_generated_nodes` (write) — see section 2.
7. `load_topic_candidates` (read) — `MATCH (i:Issue) WHERE i.issue_key IS NOT NULL RETURN i.issue_key ORDER BY key` — **every** issue, alphabetically by key.
8. For each `issue_key`, in that alphabetical order:
   - `assemble_bundle` (read) — builds the evidence bundle.
   - `call_model` (OpenAI, `client.responses.parse`) — one call.
   - `apply_resolution` — the deterministic checks.
   - `resolve_topic` — decide new topic vs. `existing_topic_slug` match against `known_topics` (in-memory, this run only).
   - `write_topic`, `write_about_topic`, `write_derived_from` (writes).
   - Per surviving event: `write_event`, `write_evidenced_by`, and `write_acted_in_event` per resolved actor (writes).
   - Per surviving causal link: `write_caused` (write).
9. `touch_pipeline_state(tx, "last_layer_build_at", generated_at)` (write).
10. `load_knowledge_state` (read) — the full topic/event/causal-link state, returned as the response body together with run counters (`calls`, `deleted_relationships`, `deleted_nodes`, `discarded_evidence`, `overflow_events`, `overflow_links`, `token_usage`).

**Subset runs: not possible.** `load_topic_candidates` has no parameter and no filter; the endpoint accepts no request body or query string. Every call to `POST /api/knowledge/build` processes **every** `Issue` with a non-null `issue_key` in the graph, and — see section 2 — deletes **all** prior Task 06 output first, unconditionally. There is no way to scope a build to one issue, one source group, or a time window without changing the code.

**Progress reporting: none, beyond the final result.** No polling endpoint, no Server-Sent Events, no WebSocket. The frontend shows only a static "running" label for the request's duration and replaces the whole state with the final JSON on completion (or shows `error` on failure). For N issues this means N sequential OpenAI calls happen invisibly inside one HTTP request with no intermediate feedback — on a larger corpus this endpoint would time out or simply leave the user staring at "Building knowledge layer..." for a long time with no per-issue progress.

**Docs correction worth flagging:** `docs/GRAPH_DATA_HANDOFF.md` states `/api/knowledge/build` answers "`503` with an explanatory message when `OPENAI_API_KEY` is not configured." The actual code raises `MissingApiKeyError` (a plain `RuntimeError` subclass) inside `run_topic_event_extraction`, which is caught by `api_knowledge_build`'s bare `except Exception as error: return jsonify({"error": str(error)}), 500` — it returns **500**, not 503. (`/api/ai/chat` does correctly return 503 for its own missing-dependency case, which is likely where that doc sentence got generalized from.)

---

## 2. The delete-and-rebuild step

Verbatim, `backend/topic_event_extraction.py` lines 546–561:

```python
def delete_generated_relationships(tx):
    result = tx.run("""
        MATCH ()-[r]->() WHERE r.generated_by = $version
        DELETE r
        RETURN count(r) AS deleted
    """, {"version": EXTRACTION_VERSION})
    return result.single()["deleted"]


def delete_generated_nodes(tx):
    result = tx.run("""
        MATCH (n) WHERE n.generated_by = $version
        DETACH DELETE n
        RETURN count(n) AS deleted
    """, {"version": EXTRACTION_VERSION})
    return result.single()["deleted"]
```

Called from `run_topic_event_extraction`:

```python
deleted_relationships = session.execute_write(delete_generated_relationships)
deleted_nodes = session.execute_write(delete_generated_nodes)
```

with `EXTRACTION_VERSION = "topic-event-extraction-v1"` (module constant, line 50).

**Which labels/types are matched, and on what property.** Neither query names a label or relationship type at all — `MATCH ()-[r]->()` and `MATCH (n)` match *anything*. The only filter is the property `generated_by = "topic-event-extraction-v1"`, checked on every relationship and every node in the entire graph. It is unscoped by label/type in the query itself; it is scoped in *practice* only because `generated_by` happens to be written exclusively by this one pass, onto exactly `Topic`, `Event`, and the six `KNOWLEDGE_RELATIONSHIP_TYPES` (`ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED`, `ACTED_IN_EVENT`). If any future code stamped `generated_by = "topic-event-extraction-v1"` onto something else by mistake or reuse, this deletion would remove it without any label/type guard to catch that.

**Scoped by `model`? No.** `generated_by` carries only the pass-name string. `model` (`"gpt-5.6-terra"`) is written as a separate property (`t.model`, `e.model`) but never appears in either `WHERE` clause. A run using a different `OPENAI_MODEL` value would still delete every node/relationship from all prior runs regardless of which model produced them. Scoping is by pass name only.

**A `Topic`/`Event` node with inbound relationships from outside the derived layer.** `DETACH DELETE` unconditionally strips every relationship touching the node (in *and* out) before deleting the node — Neo4j's `DETACH DELETE` never fails or blocks on existing relationships, by design. So such a node would be deleted along with all its relationships, silently, regardless of who created those relationships. In the current codebase this scenario cannot occur anyway: the only inbound edges to `Topic`/`Event` (`ABOUT_TOPIC`, `EVENT_OF_TOPIC`) are themselves written by this same pass with `generated_by` set, so they are already removed by `delete_generated_relationships` in the line immediately before `delete_generated_nodes` runs. But the node-deletion query itself would not protect a hypothetical future case where something else pointed at a `Topic`/`Event` node.

**Task 05 output: untouched, by property-name design, not by explicit exclusion.** Task 05 relationships (`MENTIONS_ISSUE`/`MENTIONS_PULL_REQUEST`/`MENTIONS_DOCUMENT`) carry `extracted_by`, `matched_text`, `source_property`, `extracted_at`, `derived` — confirmed against the live schema dump — and **no `generated_by` property at all**. Since both deletion queries filter strictly on `r.generated_by = $version` / `n.generated_by = $version`, and Task 05 edges simply don't have that key, they never match. This is exactly what `docs/GRAPH_DATA_HANDOFF.md`'s re-runnability section already claims ("Deletion is by `generated_by` only, never by relationship type, so a Task 05 reference edge ... is never touched"), and the code confirms it precisely.

---

## 3. Slug generation as it stands

**Pydantic schema fields — verbatim, `backend/topic_event_extraction.py` lines 71–93:**

```python
class EventOut(BaseModel):
    slug: str
    name: str
    event_type: Literal["created", "decided", "blocked", "changed", "resolved", "regressed", "other"]
    occurred_at: str
    summary: str
    evidence: list[str]
    actor_names: list[str] = Field(default_factory=list)

class TopicOut(BaseModel):
    slug: str
    name: str
    topic_type: Literal["requirement", "defect", "incident", "decision", "other"]
    summary: str
    existing_topic_slug: Optional[str] = None
```

Both `slug` fields are plain `str` — no `Field(pattern=...)`, no `@validator`/`@field_validator`, no length constraint. Searching the whole file for any post-processing of `.slug` after `response.output_parsed` is obtained (`resolve_topic`, `apply_resolution`, every `write_*` function) turns up none — the model's raw string is used verbatim as a Cypher parameter and as a Python dict key. No lowercasing, no whitespace stripping, no character-set normalization anywhere.

**`EXTRACTION_INSTRUCTIONS` — quoted in full, lines 102–124 (already reproduced verbatim above in the request; repeating the relevant fact):** the word "slug" does not appear anywhere in this instruction text. The model is never told what a slug should look like, is given no format example (no "e.g. `auth-session-timeout`"), and is given no length guidance. Whatever shape `slug` takes — kebab-case, a sentence, an emoji, anything a `str` can hold — comes entirely from the model inferring intent from the Pydantic field *name* inside the structured-output JSON schema that `client.responses.parse(text_format=TopicEventExtractionOut)` generates, not from prompt instructions.

**`existing_topic_slug` mechanism — verbatim, lines 465–471:**

```python
def resolve_topic(topic_out, known_topics):
    """Returns `(slug, is_new)`. `existing_topic_slug` is honoured only on an exact match."""
    if topic_out.existing_topic_slug and topic_out.existing_topic_slug in known_topics:
        return topic_out.existing_topic_slug, False
    if topic_out.slug in known_topics:
        return topic_out.slug, False
    return topic_out.slug, True
```

`known_topics` is passed into the model call as `existing_topics` in the JSON payload (`[{"slug": ..., "name": ...} for slug, name in known_topics.items()]`, in `call_model`), and the match on the way back is **exact Python string equality against a dict key** — no case-folding, no trimming, no fuzzy matching. The candidate set (`known_topics`) is a plain local `dict` built up only inside the single call to `run_topic_event_extraction`, populated the moment a new topic is written (`known_topics[topic_slug] = parsed.topic.name`, right after `write_topic`) — it is **never loaded from Neo4j**. A topic created in a *previous* run is invisible to this mechanism entirely; only topics already resolved earlier in the *current* run's issue loop can be matched.

**Graph write — `MERGE`, keyed purely on slug (verbatim, lines 564–570, 596–608):**

```python
def write_topic(tx, props):
    tx.run("""
        MERGE (t:Topic {slug: $slug})
        SET t.name = $name, t.topic_type = $topic_type, ...
    """, props)

def write_event(tx, topic_slug, event, generated_at):
    tx.run("""
        MATCH (t:Topic {slug: $topic_slug})
        MERGE (e:Event {topic_slug: $topic_slug, slug: $slug})
        SET e.name = $name, ...
    """, ...)
```

`Topic` merges on `slug` alone; `Event` merges on the composite `(topic_slug, slug)` — exactly the two Neo4j uniqueness constraints (`topic_slug`, `event_key`).

**Would anything else break if slug format changed?** No occurrence of `.slug` (or a raw `"slug"` string key) in `backend/app.py` beyond passthrough (`/api/knowledge` returns whatever `load_knowledge_state` produces, which is whatever is in the property, untyped). In the frontend, `KnowledgeTopic.slug` / `KnowledgeEvent.slug` are typed as plain `string` and used only as a React list `key` prop and for equality filtering (`events.filter((event) => event.topic_slug === topicSlug)`) — pure opaque-string usage, no parsing, no regex, no assumed format. No test suite exists anywhere in the repo that references slugs. **Conclusion: the system treats `slug` as an opaque unique string everywhere it is consumed. Changing how it is generated — as long as uniqueness per the two existing Neo4j constraints is preserved — is a low-blast-radius change confined entirely to `backend/topic_event_extraction.py`.**

---

## 4. Feasibility of deterministic slugs

**Proposal A — topic slug from the issue key.**

`issue_key` is in scope at every point a `Topic` is written: the outer loop in `run_topic_event_extraction` is `for issue_key in issue_keys:`, and `write_about_topic(tx, issue_key, topic_slug, generated_at)` already receives it directly, on the same line that also calls `write_topic`. Deriving `topic_slug` deterministically from `issue_key` (e.g. a normalized `issue_key.lower()`) requires no new data plumbing.

The multi-issue-one-topic case is real (confirmed in the fixture: `issue-001`/AUTH-17 and `issue-002`/AUTH-19 resolved to one `Topic`). Under Proposal A, whichever issue is processed *first* would set the topic's slug; later issues matching via `existing_topic_slug` would not change it. `load_topic_candidates` orders issues deterministically — `ORDER BY key` (alphabetical on `issue_key`) — so for a **fixed set of issues**, the "first" issue is stable across reruns. The caveat is that this only holds if the *model's own decision* about which issues share a topic is also stable across reruns — if a rerun decides AUTH-19 belongs to its own topic instead of merging into AUTH-17's (a model judgment call, not a deterministic function), the derived slug set would still shift even though the *derivation rule* is deterministic. Slug determinism under Proposal A is necessary but not sufficient for full stability; it removes the free-text randomness but not the topic-membership randomness.

**Proposal B — event slug hashed from stable content.**

Of the fields on `EventOut`:

- `event_type` — constrained to a 7-value `Literal`, so its *range* is fixed, but which value the model picks for "the same" real event across two runs is a classification judgment, not a deterministic function of the input text. Not proven stable.
- `occurred_at` — the prompt instructs "must come from a timestamp present in the evidence, never invented" (`EXTRACTION_INSTRUCTIONS`), so its *value space* is constrained to timestamps that actually exist in the bundle (which is deterministic, since the bundle itself comes straight from Neo4j via read-only Cypher and is identical for identical source data). But the model still chooses *which* of possibly several valid evidence timestamps to report for a multi-source event — not proven byte-identical across runs without an actual test.
- Evidence identifier sets (the `evidence: list[str]` field, which becomes `EVIDENCED_BY` targets) — which specific bundle items the model decides to cite for "this" event is itself a model choice, not a deterministic derivation.

None of `EventOut`'s own fields are guaranteed reproducible by the code; every one of them is model output, just with varying degrees of prompt-constrained value space. The only thing in this whole pipeline that is deterministic by construction is the **bundle itself** (assembled by `assemble_bundle` via read-only Cypher against unchanged source data) — not any field the model returns about it.

**Proposal C — stability through the evidence set (`EVIDENCED_BY` targets).**

**Confirmed**, and this is the strongest of the three candidates. The bundle item `identifier` values that eventually become `EVIDENCED_BY` targets are built by `to_bundle_item` (lines 270–347) directly from each source node's own natural key — e.g. `IssueVersion` uses `props.get("display_name")`, which is written at import time as `"<issue_key> v<version_number>"`, itself derived from exactly the fields in that label's Neo4j uniqueness constraint (`(source_instance, issue_id, version_number)`, per `GRAPH_SCHEMA_HANDOFF.md` section 2). Same pattern for `TeamsTranscriptSegment.segment_id`, `DocumentVersion.display_name`, `PullRequestReview.source_id`, `CodeChange.display_name`, `MailMessage.message_id`, `SlackMessage`'s `"{message_id} v{version_number}"`. None of these identifiers are model output — they are import-time-deterministic strings sourced from SQL primary keys. So the *pool* of possible evidence identifiers for a given issue's bundle is 100% reproducible across reruns of unchanged source data.

What is **not** confirmed without an actual rerun (section 5) is whether the model consistently selects the *same subset* of that pool for what a human would call "the same event" every time it's asked. A hash of `(topic_slug, sorted(evidence_identifiers))` would be stable *if* the model's evidence selection is stable; it would not be if the model sometimes cites 2 of 3 relevant items and sometimes cites all 3 for the same underlying event. This is exactly the empirical question section 5 is designed to answer, and Proposal C cannot be fully validated by code reading alone — it can only be shown to rest on a stable foundation (the identifiers themselves), which the other two proposals partially share but with an extra unstable layer (`event_type`, free-text `slug`) on top.

---

## 5. Observed non-determinism

**Run, with explicit go-ahead.** `run_topic_event_extraction` was called twice in direct succession (not through the Flask server, same function the endpoint calls), against the unchanged fixture (2 issues, `AUTH-17`/`AUTH-19`). Both runs used `gpt-5.6-terra`, reasoning effort `medium`, no `seed`/`temperature` pinned (confirmed below). Full before/after state was captured with read-only Cypher between the runs.

**Node counts per label:**

| | Run 1 | Run 2 |
| --- | --- | --- |
| `Topic` | 1 | 1 |
| `Event` | 11 | 12 |
| `CAUSED` | 6 | 7 |
| `EVIDENCED_BY` | 27 | 29 |
| `ACTED_IN_EVENT` | 17 | 18 |
| OpenAI calls | 2 | 2 |
| Output tokens | 2721 | 2948 |
| Input tokens | 9228 | 9228 (identical — the bundle text sent in is deterministic, only the model's output length varies) |

**`Topic.slug`: identical across both runs** — `administrator-session-lifetime-policy` in both, with an unchanged `name` ("Administrator session lifetime policy") and a paraphrased-but-equivalent `summary`. Both `AUTH-17` and `AUTH-19` resolved to this same topic in both runs (`about_topic` identical in both states). **This is a genuinely encouraging result for Proposal A** — but it is one observation, not a guarantee; nothing in the code forces this, the model simply landed on the same short phrase both times for a topic with an unambiguous, narrow theme. A more ambiguous or multi-themed issue set would be expected to show more topic-slug drift than this fixture did.

**`Event.slug`: not stable.** The set of event slugs is almost entirely different between runs:

Run 1 slugs: `northwind-reports-admin-session-expiry`, `administrator-inactivity-session-policy-documented`, `initial-sixty-minute-fixed-expiry-criteria`, `security-rejects-fixed-window-and-blocks-auth-17`, `session-policy-criteria-rewritten`, `auth-17-resumed-after-billing-incident`, `web-refresh-path-only-coverage-noted`, `web-session-policy-implementation-merged`, `mobile-session-expiry-regression-reported`, `mobile-administrator-fixed-expiry-regression-reported`, `mobile-refresh-inactivity-fix-created` (11).

Run 2 slugs: `northwind-reports-admin-session-expiry`, `administrator-session-policy-established`, `initial-fixed-sixty-minute-requirement`, `fixed-window-pr-opened`, `security-rejects-fixed-expiry`, `auth-17-blocked-and-requirement-rewritten`, `work-resumes-on-refresh-path`, `web-refresh-scope-identified`, `web-session-policy-change-delivered`, `mobile-admin-session-regression-discovered`, `mobile-administrator-fixed-expiry-regression-reported`, `mobile-refresh-policy-fix-proposed` (12).

Only **2 of 11–12 slugs matched exactly** across runs (`northwind-reports-admin-session-expiry` and `mobile-administrator-fixed-expiry-regression-reported`) — almost certainly convergent phrasing rather than a deterministic mechanism, since every other conceptually-matching event pair got a different slug (e.g. run 1's `session-policy-criteria-rewritten` vs. run 2's `auth-17-blocked-and-requirement-rewritten` describe overlapping territory but split the story at a different point). **Event count itself differs (11 vs. 12)** — run 2 split out a distinct "fixed-window PR opened" event (`fixed-window-pr-opened`, evidenced by `seg-002`/`slack-004`) that run 1 folded into its neighbouring events instead. This is not merely a slug-naming problem — **the model draws different event boundaries on different runs over identical input**, so no slug-derivation scheme (hashing, deterministic naming, anything) can produce matching keys for events that don't consistently exist as distinct entities in the first place.

**`CAUSED` edges: not stable, consistent with the event instability above.** 6 links in run 1, 7 in run 2, referencing different event-slug pairs by construction (since the event slugs themselves differ). The causal *reasoning* is similar in substance between runs (e.g. both runs report "security rejected the fixed window" → "work was blocked/rewritten" as a link), but the edges are keyed on the run's own event slugs, so they cannot be compared or merged across runs without a semantic-similarity step — exact-match comparison finds zero shared `CAUSED` edges between the two runs' key pairs.

**Reading against section 4's three proposals:** this confirms the split predicted there. **Proposal A (topic slug from issue key) is well-supported** — even the model's own free-text slug converged in this test, and deriving it from `issue_key` directly would remove the residual risk entirely. **Proposal B (event slug hashed from `EventOut` fields) is not supported** — `event_type`/`occurred_at` alone cannot disambiguate `run 2`'s extra `fixed-window-pr-opened` event from being folded into neighbours in run 1, because the *set of events itself* isn't stable, not just their labels. **Proposal C (identity via the `EVIDENCED_BY` evidence set) is the only one of the three that could work, and only partially**: `mobile-administrator-fixed-expiry-regression-reported` kept the same slug *and* the same evidence targets (`AUTH-19`, `AUTH-19 v1`, `slack-010`) in both runs, which is a real positive signal for that one event — but most other events do not have matching evidence sets between runs either (e.g. run 1's `security-rejects-fixed-window-and-blocks-auth-17` cites `AUTH-17 v3, comment-002, seg-003, seg-004, seg-005`; run 2 splits the same material across `security-rejects-fixed-expiry` citing `comment-002, seg-003` and `auth-17-blocked-and-requirement-rewritten` citing `AUTH-17 v3, doc-001 v2, seg-004, seg-005` — same underlying facts, different grouping). Evidence-set identity would need a fuzzy/overlap-based matching step (e.g. Jaccard similarity above a threshold), not exact-set equality, to usefully align events across runs.

**Bottom line for the embedding decision:** embed `Topic` nodes keyed by a slug derived from `issue_key`(s) — this is now empirically supported, not just structurally plausible. **Do not build an embedding-identity scheme for `Event` nodes around slug or evidence-set matching** — the event boundaries themselves are not reproducible across reruns of this pass with its current prompt and settings. An embedding pass over `Event.summary` would need to either (a) re-embed everything on every knowledge-layer rebuild (cheap enough at this scale, 10–12 events), treating `Event` embeddings as fully disposable and rebuilt in lockstep with the layer itself, or (b) the extraction prompt itself would need to be changed to make event segmentation more consistent before event-level embedding identity becomes worth engineering — that is a prompt-engineering problem, not a slug-scheme problem.

**Determinism knobs, confirmed from code:** `client.responses.parse(model=OPENAI_MODEL, reasoning={"effort": OPENAI_REASONING_EFFORT}, instructions=EXTRACTION_INSTRUCTIONS, input=..., text_format=TopicEventExtractionOut)` (`backend/topic_event_extraction.py` lines 441–447) — no `temperature`, no `seed` parameter passed. This run used whatever the API defaults to for both. Whether `gpt-5.6-terra` supports a `seed` parameter for reproducibility is `UNKNOWN` from this codebase and wasn't tested (pinning it would be a code change, not something togglable from outside). Given the magnitude of the observed drift (different event *counts*, not just different phrasing), it's unlikely that a `seed` alone would fully close the gap — event boundary decisions look like a genuine reasoning-level judgment call, not sampling noise on top of an otherwise-fixed answer.

**Side effect of this experiment:** the live Neo4j database's Task 06 layer now holds run 2's output (12 events, 7 causal links, 1 topic), not the single-run state that was there before this test began. This is the expected and requested outcome of "run it twice and report what differs" — flagging it so it's not mistaken for an unrelated change if `GRAPH_SCHEMA_HANDOFF.md`'s counts are compared against the database later without re-running the introspection.

---

## 6. Where the embedding pass should sit

**How the two existing passes are triggered — same shape, both stateless-server-side / synchronous-request:**

| | Task 05 (`reference_extraction.py`) | Task 06 (`topic_event_extraction.py`) |
| --- | --- | --- |
| Endpoint | `POST /api/references/extract` | `POST /api/knowledge/build` |
| Frontend control | `ReferenceExtractionPanel`, tab "Reference extraction" | `KnowledgeLayerPanel`, tab "Knowledge layer" |
| Frontend trigger | `fetch("/api/references/extract", { method: "POST" })` (main.tsx ~line 1192) | `fetch("/api/knowledge/build", { method: "POST" })` (main.tsx line 1306) |
| Execution | Synchronous in-request | Synchronous in-request |
| `PipelineState` field written | `last_extraction_at` | `last_layer_build_at` |
| Progress reporting | None (same all-or-nothing pattern) | None |

**Are the `PipelineState` timestamps used to enforce ordering, or only displayed?** Only advisory, never enforced. `needs_rerun(state)` (Task 05) and `needs_layer_rerun(state)` (Task 06) each compute a boolean by comparing two timestamps (`last_import_at > last_extraction_at`, `last_extraction_at > last_layer_build_at`) and that boolean is returned in the API payload purely for the frontend to render a warning: a CSS class (`reference-build-button-stale`-equivalent / `knowledge-build-button-stale`) and a text banner ("References have changed since the last build. Run it again."). **Nothing disables the button or blocks the backend call based on staleness** — a user (or a script) can call `POST /api/knowledge/build` at any time regardless of whether references were re-extracted since the last import, and it will run anyway, just possibly against stale reference edges.

**Frontend tab structure — verbatim, `main.tsx` ~lines 1460–1488:**

```tsx
const [activeInnerTab, setActiveInnerTab] = useState<"step1" | "step2">("step1");
// ...
<div className="center-tabs center-tabs-inner" role="tablist" aria-label="Build graph layers tabs">
  <button ... onClick={() => setActiveInnerTab("step1")}>Reference extraction</button>
  <button ... onClick={() => setActiveInnerTab("step2")}>Knowledge layer</button>
</div>
<div className="center-tab-panel" role="tabpanel">
  {activeInnerTab === "step1" ? <ReferenceExtractionPanel /> : <KnowledgeLayerPanel />}
</div>
```

A third pass would need: (1) widen `activeInnerTab`'s type to `"step1" | "step2" | "step3"`, (2) add a third `<button>`, (3) change the ternary to a 3-way conditional (or a small lookup), (4) write a new panel component mirroring `KnowledgeLayerPanel`'s shape exactly — local `state`/`isRunning`/`error`/`justRan` state, a `GET` on mount, a `POST` on button click, full-state replacement on response. This is a small, mechanical addition; the existing two panels are near-identical in structure, so a third follows the same template.

**Per-node stamp precedent.** Task 05 stamps *relationships* only (it creates no nodes): `r.derived = true, r.extracted_by = "reference-extraction-v1", r.matched_text, r.source_property, r.extracted_at`. Task 06 stamps *both* nodes and relationships it creates: `derived = true, generated_by = "topic-event-extraction-v1", model = "gpt-5.6-terra", generated_at = <iso timestamp>` (identical property names on `Topic`, `Event`, and all six knowledge relationship types). **This is the exact shape to mirror for an embedding pass's stamp** — with one caution: Task 06's stamp doubles as "this thing was invented, not copied from source" (`derived: true`), which is true for `Topic`/`Event` but would be **false** for an embedding on `IssueVersion.description` or `DocumentVersion.body` — those nodes are source-derived facts, not invented content. An embedding pass should use its own property names (e.g. `embedding`, `embedding_model`, `embedded_at`, `embedding_source_hash`) rather than reusing `derived`/`generated_by`, both to avoid the semantic clash and because — per section 2 — anything carrying `generated_by = "topic-event-extraction-v1"` gets deleted on every Task 06 rebuild, which an embedding on a *source* node must never be exposed to.

---

## 7. Change tracking for incremental embedding

**Does any node carry a content hash or an `updated_at` distinct from source timestamps?** No. Confirmed against the live property/type dump in `GRAPH_SCHEMA_HANDOFF.md` section 1: no label has a `content_hash`, `updated_at`, or `imported_at` property. `version_at` (present on `SlackMessage`, `IssueVersion`, `IssueComment`, `DocumentVersion`, `PullRequest`, `PullRequestReview`) is a **source-system timestamp**, copied verbatim from the corresponding SQL column (e.g. `issue_versions.version_at`) — it is the simulated source's own "this version was recorded at" time, not a record of when the Neo4j import last touched the node. There is no per-node import timestamp anywhere; only `PipelineState.last_import_at` exists, and it's a single global singleton value, not attached to individual nodes.

**Can a re-import tell "touched but unchanged" from "never touched since first import"?** No. Every import write (verified in `viewer/app.py`, e.g. the `MailMessage` merge at lines 493–507) is `MERGE (node {key...}) SET node.field1 = $x, node.field2 = $y, ...` — an unconditional `SET` on a fixed field list, run every time the import path executes for that row, whether or not any value actually changed. Cypher's `SET` doesn't expose "did this write change anything" as a return value the code captures, and no import query compares old-vs-new before writing. So from the data alone there is no way to distinguish a node that was re-imported with identical values yesterday from one that hasn't been touched in months.

**Are `IssueVersion`/`DocumentVersion` rows append-only in practice, or could their text mutate in place?** **Intended to be append-only by convention, not enforced as append-only by anything in the schema or import code.** `docs/SQL_DATA_HANDOFF.md` documents the convention directly: "Preserve versions by inserting multiple rows with the same object ID and increasing `version_number`" — a change is supposed to always produce a new row with a new `version_number`, never an `UPDATE` to an existing version's text. But nothing enforces this: the SQL DDL (`scripts/setup_postgres_schema.py`, reproduced in `docs/SQL_DATA_HANDOFF.md`) has no trigger, no `UPDATE`-blocking rule, no immutability constraint on `issue_versions`/`document_versions` — an `UPDATE issue_versions SET description = ... WHERE version_number = 3` is perfectly legal SQL against this schema. If that ever happened, the Neo4j import's `MERGE ... SET` pattern would silently overwrite the corresponding node's properties on the next import run, with the old text gone and no diff or history retained anywhere (Neo4j has no versioning of its own here; the "version" concept is entirely a modeling convention borrowed from the SQL rows, not an append-only guarantee).

**Decision this supports:** incremental embedding cannot key on any existing property — there is no reliable "this text changed since last embedded" signal anywhere in the current schema. It needs a **new property written by the embedding pass itself**, most directly a hash of the exact text that was embedded (e.g. `node.embedding_source_hash = sha256(text)`), stored alongside `node.embedding`. On each embedding-pass run: recompute the hash of the current text, compare against the stored hash, skip if equal, re-embed and overwrite both `embedding` and `embedding_source_hash` if different. This also correctly handles the append-only-violation edge case above — if a version's text *is* mutated in place against convention, the hash mismatch would still catch it and trigger re-embedding, which a naive "embed once, never revisit" approach would miss silently.

---

## Note on section 5

Run with explicit confirmation, via `run_topic_event_extraction` called directly (not through the Flask server), capturing before/after state with the same read-only pattern used in `GRAPH_SCHEMA_HANDOFF.md`. Results are in section 5 above. This left the database's Task 06 layer at run 2's output rather than what was there before this file was started — see the "Side effect" note at the end of section 5.
