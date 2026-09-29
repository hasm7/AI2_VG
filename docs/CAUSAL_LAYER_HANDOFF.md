# Layer 4: Root Cause & Impact Layer

The fourth graph-building step (code, API and graph filter use the name `causal`; the UI says "Root cause & impact").
Around each topic's events it adds why things happened (`RootCause`), which code changes contributed to which events
(`CONTRIBUTED_TO`), which components each event affected (`AFFECTED_COMPONENT`), and causal links between events of
different topics (`CROSS_TOPIC_CAUSED`). It never creates, changes or deletes the Knowledge layer's `CAUSED` links.

**Verified on 2026-09-29** against `backend/causal_layer.py`, `backend/app.py`, `frontend/src/main.tsx` and the live
graph.

Code: `backend/causal_layer.py`. UI: `CausalLayerPanel`, tab `Root cause & impact layer` (fourth of seven).

## 1. Configuration

| Constant | Value |
| --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra`, effort `medium`, structured output; one call per `Topic` |
| `CAUSAL_VERSION` | `causal-layer-v1` |
| `CANDIDATE_WINDOW_DAYS` | 30 |
| `MAX_CANDIDATE_EVENTS` | 30 (events of other topics offered per topic) |
| `MAX_ROOT_CAUSES` / `MAX_CODE_CONTRIBUTIONS` / `MAX_AFFECTED_COMPONENTS` / `MAX_CROSS_TOPIC_LINKS` | 10 / 20 / 20 / 10 per topic |
| `TRUNCATE_LENGTH` | 4000 characters per text |

Prerequisites: `last_layer_build_at` and `last_architecture_build_at` (else 409 `Run Knowledge layer and Architecture
layer first.`). Missing `OPENAI_API_KEY`: 503.

## 2. Bundle per topic

`assemble_causal_bundle`:

| Part | Content | Identifier |
| --- | --- | --- |
| Topic | slug, name, type, summary | |
| Events | every event of the topic, by `occurred_at` | `event:<topic_slug>/<event_slug>` |
| Existing causal links | the topic's `CAUSED` links (read-only context, not to be re-proposed) | |
| Evidence | every node an event of the topic cites (`EVIDENCED_BY`), with its text | the shared identifiers (`docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 8) |
| Code changes | every `CodeChange` of every `PullRequest` in the topic's `DERIVED_FROM` set | `<display_name> v<version_number>` |
| Components | every `Component` in the repositories of those code changes, with their files | `component:<repository>/<slug>` |
| Candidate events | up to 30 events of **other** topics whose `occurred_at` lies within 30 days of any event of this topic, closest first | `event:<topic_slug>/<event_slug>` |

Valid evidence = the evidence identifiers plus the code change identifiers. Components and events are referenced by
their ids but are not evidence.

So a component is only in reach when the topic's issue is linked (through the reference layer) to a PR that changed
files of that component's repository. Cross-topic candidates exist only when there is a second topic with events
close in time.

## 3. Output schema

```text
CausalExtractionOut
  root_causes[]:         slug, name,
                         cause_type (requirement_gap|design_decision|implementation_gap|missing_test|process|external|other),
                         summary, explains_event_ids[], component_ids[], explanation, evidence[]
  code_contributions[]:  code_change_id, event_id,
                         contribution_type (introduced|resolved|partially_resolved|related), explanation, evidence[]
  affected_components[]: event_id, component_id, explanation, evidence[]
  cross_topic_links[]:   cause_event_id, effect_event_id, explanation, evidence[]
```

The instructions define a root cause as the underlying reason for one or more events (not the event itself) that
explains a problem, a delay, a defect, an incident or a change of plan, never a routine step that went as planned. A
cross-topic link is allowed only when one event directly caused the other (belonging to the same plan, release or
decision is not enough), the cause must not happen after the effect, and a pair is proposed at most once, in one
direction. The instructions also require evidence identifiers copied exactly and forbid general knowledge. The two
sharper rules were added on 2026-09-29, after a build on the Kvitta data produced root causes for routine onboarding
steps and a thin link from a requirement to a completed onboarding task.

## 4. Validation (per topic, then across the run)

- **Root causes:** keep only `explains_event_ids` that are events of this topic and `component_ids` in the bundle;
  drop the root cause if no explained event or no evidence remains; at most 10.
- **Code contributions:** drop unless the code change and the event (of this topic) are in the bundle and evidence
  remains; dedupe on `(code change, event)`; at most 20.
- **Affected components:** drop unless the event (this topic) and component are in the bundle and evidence remains;
  dedupe on `(event, component)`; at most 20.
- **Cross-topic links:** exactly one end must be an event of this topic and the other a candidate; drop if the cause
  is later than the effect or no evidence remains; at most 10 per topic; then deduplicated across the whole run on the
  **pair** of events, whichever direction (`dedupe_cross_topic_links`; the first proposal is kept). Before 2026-09-29
  only an identical `(cause, effect)` was removed, so two topics could produce A caused B and B caused A for the same
  pair.

## 5. Graph model

```cypher
(:Event)-[:HAS_ROOT_CAUSE {explanation, evidence}]->(:RootCause)
(:RootCause)-[:ROOT_CAUSE_IN_COMPONENT]->(:Component)
(:RootCause)-[:ROOT_CAUSE_EVIDENCED_BY]->(:SourceNode)
(:CodeChange)-[:CONTRIBUTED_TO {contribution_type, explanation, evidence}]->(:Event)
(:Event)-[:AFFECTED_COMPONENT {explanation, evidence}]->(:Component)
(:Event)-[:CROSS_TOPIC_CAUSED {explanation, evidence}]->(:Event)
```

`RootCause` key `slug` (constraint `root_cause_slug`): `name`, `display_name`, `cause_type`, `summary`, `model`. When
two topics propose the same slug, one node is kept (the first one's properties, `MERGE ... ON CREATE SET`) and both
topics' relationships attach to it. The `explanation` of a root cause is written on each `HAS_ROOT_CAUSE`. All nodes
and relationships: `derived`, `generated_by = causal-layer-v1`, `generated_at`. `CROSS_TOPIC_CAUSED` is a separate
type from `CAUSED`.

## 6. Build behaviour

1. Check prerequisites and the key; ensure the constraint.
2. For each topic: assemble, call, validate. **A failed call leaves the existing layer untouched.**
3. After all calls: delete this layer's relationships and nodes (`generated_by = causal-layer-v1`), then write root
   causes (with `HAS_ROOT_CAUSE`, `ROOT_CAUSE_IN_COMPONENT`, `ROOT_CAUSE_EVIDENCED_BY`), contributions, affected
   components, then the deduplicated cross-topic links.
4. Set `PipelineState.last_causal_build_at`.

## 7. Current state (2026-09-29)

Kvitta data, built after the two sharper prompt rules and the pair dedupe were added. 15 root causes (22
`HAS_ROOT_CAUSE`, 24 `ROOT_CAUSE_IN_COMPONENT`, 50 `ROOT_CAUSE_EVIDENCED_BY`), 31 code contributions, 53 affected
components, 6 cross-topic links, 27 within-topic `CAUSED` read from layer 2 (unchanged); 15 nodes and 186
relationships; not stale. Every rule check passes: all evidence inside the topic, no cross-topic link with the cause
after the effect or outside the 30-day window, and no pair of events linked in both directions.

| Topic | Root cause | Type | In components |
| --- | --- | --- | --- |
| duplicate payout | Retry design accepted without duplicate-submission handling | design_decision | Offline expense sync endpoint, Offline expense queue, Retry delivery |
| duplicate payout | Payout export lacked a duplicate-expense check | implementation_gap | Payout export |
| offline capture | Duplicate handling was deliberately deferred despite timeout-retry risk | design_decision | Offline expense sync endpoint, Payout export, Retry delivery |
| Fortnox | Fortnox OAuth flow change was not adopted by the client | external | Fortnox authentication, Fortnox |
| Fortnox | Fortnox developer-changelog monitoring gap | process | Fortnox authentication, Fortnox |
| Fortnox | Automatic token refresh was missing from the hotfix | implementation_gap | Fortnox authentication, Fortnox client |
| receipt reader | VAT requirement initially covered only 25% | requirement_gap | Receipt reader, Receipt VAT rules |
| receipt reader | Receipt reader implemented VAT as a 25% calculation | implementation_gap | Receipt reader, Receipt VAT rules |
| receipt reader | Multi-rate VAT validation used the wrong comparison scope | implementation_gap | Receipt VAT rules |
| image retention | Receipt image storage lacked retention and role-based access controls | implementation_gap | Receipt image storage and retention |
| approval limits | Initial money representation and boundary-test coverage gap | implementation_gap | Manager approval limits, Approval routing rules |
| onboarding | Approval button label did not describe its page-scoped behavior | implementation_gap | Approval List |
| onboarding | Approval list omitted the receipt date needed by managers | implementation_gap | Approval List |
| upload spinner | Late upload callback omitted spinner-state clearing | implementation_gap | Expense upload |
| Visma | Visma export was not ready for the 1.0 release | implementation_gap | none |

Key code contributions: `kvitta-mobile#6 src/offline/retry.ts v1` **introduced** "Duplicate payout of a train-ticket
expense reported" and "Offline retry contributed to a duplicate payout incident"; `kvitta-api#60 app/payouts/export.py
v1` **resolved** "Idempotent payout-export safeguard deployed"; `kvitta-api#55` and `kvitta-api#58` resolved the
Fortnox events.

Cross-topic links (cause -> effect):

| Cause | Effect |
| --- | --- |
| Offline queue and server sync support delivered (offline, 02-20) | Duplicate payout of a train-ticket expense reported (duplicate, 03-09) |
| Offline queue with retry was accepted despite identified duplicate risk (duplicate, 02-12) | Offline retry contributed to a duplicate payout incident (offline, 03-09) |
| Permanent Fortnox token-refresh fix merged (Fortnox, 03-10) | Go approved for Kvitta 1.0 (release, 03-20) |
| Idempotent payout-export safeguard deployed (duplicate, 03-18) | Go approved for Kvitta 1.0 (release, 03-20) |
| Mobile duplicate-submission protection deferred to version 1.1 (duplicate, 03-20) | Go approved for Kvitta 1.0 (release, 03-20) |
| Kvitta 1.0 scope and release blockers set (release, 03-10) | Visma export moved from 1.0 to 1.1 (Visma, 03-10) |

Wording varies between rebuilds.

## 8. What the data needs for this layer

- **Topics with PRs linked to their issues** (the issue names the PR, or the PR names the issue), so code changes and
  components are in reach.
- **Stories with an underlying reason**, stated or clearly implied in the text: a requirement that missed something, a
  design decision later reversed, an implementation that covered only part of the system, a missing test, a warning
  nobody acted on, an external dependency.
- **Two or more topics whose events lie within 30 days of each other, where one plausibly caused the other** (an
  outage caused by a config change in another project; a billing incident that delayed session work), with text that
  says so, for `CROSS_TOPIC_CAUSED`. The Kvitta data has ten topics and produces six such links (section 7).
- Code changes whose summaries say what they introduced or fixed.

## 9. API

`GET /api/causal`: `last_layer_build_at`, `last_architecture_build_at`, `last_causal_build_at`, `needs_rerun`,
`stale_reasons`, `counts` (`root_causes`, `code_contributions`, `affected_components`, `cross_topic_links`,
`within_topic_links`), `root_causes`, `root_cause_links` (one row per `HAS_ROOT_CAUSE`), `code_contributions`,
`affected_components`, `cross_topic_links`, `within_topic_links` (the Knowledge layer's `CAUSED`, read-only),
`relationships`.

`POST /api/causal/build`: the same plus `built_at`, `deleted_relationships`, `deleted_nodes`, `calls`, `model`,
`discarded_evidence`, `token_usage`. Errors 409, 503, 500.

## 10. UI

Tab `Root cause & impact layer`. Button `Build root cause & impact layer` / `Building root cause & impact layer...`.
Description: `Finds the root causes behind events, the code changes that contributed to them, and the components they
affected.` Counts under `Nodes and relationships:`; the within-topic count separately under `Read from Knowledge
layer:`. Tables: `Root causes (node)`, `Relationships (all relationship types)`, `Root cause links (relationship:
HAS_ROOT_CAUSE)`, `Code contributions (relationship: CONTRIBUTED_TO)`, `Affected components (relationship:
AFFECTED_COMPONENT)`, `Cross-topic links (relationship: CROSS_TOPIC_CAUSED)`, `Within-topic links (relationship:
CAUSED, from Knowledge layer)`.

Graph filter `Causal` (button `Causes`): `CAUSED`, `CROSS_TOPIC_CAUSED`, `HAS_ROOT_CAUSE`, `ROOT_CAUSE_IN_COMPONENT`,
`ROOT_CAUSE_EVIDENCED_BY`, `CONTRIBUTED_TO`, `AFFECTED_COMPONENT`.

## 11. Boundaries

- Reads and writes Neo4j only; never PostgreSQL.
- Never writes `CAUSED`; deletes only by `generated_by = causal-layer-v1`.
- Depends on layer 2 (events, `CAUSED`, `EVIDENCED_BY`, `DERIVED_FROM`) and layer 3 (components).
