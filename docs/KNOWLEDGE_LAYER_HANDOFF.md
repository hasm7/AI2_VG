# Layer 2: Knowledge Layer (Topics and Events)

The second graph-building step. For each issue it assembles every connected piece of evidence into one bundle, asks a
model what the issue is about (a `Topic`), what happened (`Event`s), who took part and which events caused which, and
writes only what survives deterministic validation. The model proposes; the code decides what is written.

**Verified on 2026-09-29** against `backend/topic_event_extraction.py`, `backend/app.py`, `frontend/src/main.tsx` and
the live graph.

Code: `backend/topic_event_extraction.py`. API: `backend/app.py`. UI: `KnowledgeLayerPanel`, tab `Knowledge layer`
(second of seven).

## 1. Why it exists

The reference layer only sees identifiers. The most important sentences often name none: in the current data Nina's
question in the offline design meeting (`seg-009`, "what happens when a request times out after it has already
arrived") names no issue, and neither does her warning in `review-008`. This layer reads whole conversations around an
issue (the meeting joins because `seg-010` names `KV-2`; the review joins because `kvitta-mobile#6` names `KV-2`) and
records the story as events with evidence, so questions like "why was the same expense paid twice" and "did anyone
warn about it" have an answer in the graph.

## 2. Configuration

| Constant | Value |
| --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra` (OpenAI Responses API, structured output) |
| `OPENAI_REASONING_EFFORT` | `medium` |
| `EXTRACTION_VERSION` | `topic-event-extraction-v1` (the `generated_by` stamp) |
| `REFERENCE_EXTRACTOR_NAME` | `reference-extraction-v1` (only edges with this `extracted_by` are followed) |
| `MAX_EVENTS_PER_TOPIC` | 15 (per issue call; more are counted as overflow and dropped) |
| `MAX_CAUSAL_LINKS_PER_TOPIC` | 15 (same) |

`OPENAI_API_KEY` is required. Without it the build fails; the route answers **500** with the message (it does not map
the error to 503 as the other LLM layers do).

## 3. Topic candidates

One candidate per `Issue` with an `issue_key`, processed in `issue_key` **text** order (`KV-1`, `KV-10`, `KV-11`,
`KV-12`, `KV-2`, ... `KV-9`). One model call per issue. Several issues can share one topic (section 6).

## 4. Evidence bundle (per issue)

`assemble_bundle(tx, issue_key)`:

1. The `Issue`, all its `IssueVersion` nodes and all its `IssueComment` nodes ("own" nodes).
2. Every node linked to an own node by `MENTIONS_ISSUE`, `MENTIONS_PULL_REQUEST` or `MENTIONS_DOCUMENT`
   (`extracted_by = reference-extraction-v1`), **in either direction**: nodes that name the issue, and PRs, documents
   and other issues that the issue, its versions or comments name. One hop only.
3. For each reached node:
   - `PullRequest`: add all its `PullRequestReview` and `CodeChange` nodes.
   - `Document`: add all its `DocumentVersion` nodes.
   - `TeamsTranscriptSegment`: add its `TeamsMeeting` and **every** segment of that meeting, in `sequence_number`
     order (a meeting is one conversation; half of it invites the model to invent the other half).
4. Sort all items by `occurred_at` (text order), undated items last, then by identifier.

**What reaches a bundle, and what does not.** A source reaches the issue's bundle if it names the issue key, or is
named by the issue, its versions or comments, or is a review, code change, document version or meeting segment of
something that did. A Slack message that names only `kvitta-api#58` (not the issue) is **not** in the bundle, even if
the issue names that PR: the expansion is one hop from the issue's own nodes. Mails reach a bundle only by naming the
issue key. Other issues reach it as a bare `Issue` node (without their versions or comments).

Each bundle item carries:

| Label | Identifier | `occurred_at` | `author` | Text given to the model |
| --- | --- | --- | --- | --- |
| `Issue` | `issue_key` | `created_at` | `creator_name` | title + description |
| `IssueVersion` | `display_name` (`KV-7 v3`) | `version_at` | `changed_by_name` | title + description + acceptance criteria |
| `IssueComment` | `comment_id` | `created_at` | `author_name` | body |
| `MailMessage` | `message_id` | `sent_at` | `sender_name` | subject + body |
| `SlackMessage` | `message_id v<version>` | `sent_at` | `author_name` | body |
| `TeamsMeeting` | `meeting_id` | `started_at` | none | title |
| `TeamsTranscriptSegment` | `segment_id` | the meeting's `started_at` | `speaker_name` | body |
| `Document` | `document_id` | `created_at` | `author_name` | title + body |
| `DocumentVersion` | `display_name` (`doc-001 v2`) | `version_at` | `author_name` | title + body + change summary |
| `PullRequest` | `display_name` (`kvitta-api#58`) | `created_at` | `author_name` | title + description |
| `PullRequestReview` | `source_id` | `created_at` | `author_name` | body |
| `CodeChange` | `display_name` (`kvitta-api#58 app/integrations/fortnox/client.py`) | none | none | `file_path (change_type)` + before + after summary |

Texts are not truncated here. Identifiers that collide (two items with the same identifier) keep only the first item;
`CodeChange` versions of the same file share one identifier, so only one of them is in the bundle.

## 5. Model call and output schema

Input (JSON): the issue (`issue_key`, `title`, `status`, `created_at`), `existing_topics` (slug and name of topics
already created in this run), and `bundle_items_in_chronological_order` (identifier, label, display name, author,
occurred_at, text). The instructions require every event to be grounded in the bundle, `occurred_at` taken from a
timestamp in the evidence, identifiers copied exactly, few well-evidenced events, a causal link only where the text
indicates the connection, and reuse of an existing topic when the issue belongs to it.

```text
TopicEventExtractionOut
  topic:        slug, name, topic_type (requirement|defect|incident|decision|other), summary, existing_topic_slug?
  events[]:     slug, name, event_type (created|decided|blocked|changed|resolved|regressed|other),
                occurred_at, summary, evidence[], actor_names[]
  causal_links[]: cause_event_slug, effect_event_slug, explanation, evidence[]
```

## 6. Validation (`apply_resolution`, `cause_after_effect`) and topic resolution

Nothing is written without passing these checks:

1. Only the first 15 events and 15 causal links are considered.
2. Evidence identifiers not in the bundle are discarded (counted as `discarded_evidence`).
3. An event with no remaining evidence is dropped.
4. An event whose `occurred_at` does not parse as an ISO timestamp is dropped.
5. `actor_names` are resolved **by name only** through the person registry (`resolve_person_key(name=...)`); an
   unresolved name is ignored. No person is created.
6. A causal link is dropped if either event was dropped, if cause and effect are the same, if the cause's
   `occurred_at` is later than the effect's (`cause_after_effect`; equal times are allowed, and a date without an
   offset is compared by wall-clock time against one with an offset), or if no evidence remains. The time rule was
   added on 2026-09-29, after a build on the Kvitta data produced a link from a hotfix back to the outage it fixed.

Topic resolution (`resolve_topic`): `existing_topic_slug` is honoured only when it exactly matches a topic created
earlier in this run; otherwise a proposed `slug` that already exists in this run is reused; otherwise the topic is new.
A reused topic keeps its first name, while `topic_type` and `summary` are overwritten by the later issue's proposal.
Events are keyed by `(topic_slug, slug)`: if two issues of the same topic propose the same event slug, the second
overwrites the first's properties and adds its evidence.

## 7. Graph model

```cypher
(:Issue)-[:ABOUT_TOPIC]->(:Topic)
(:Topic)-[:DERIVED_FROM]->(:SourceNode)         // every item of the issue's bundle
(:Event)-[:EVENT_OF_TOPIC]->(:Topic)
(:Event)-[:EVIDENCED_BY]->(:SourceNode)         // cited and validated evidence
(:Person)-[:ACTED_IN_EVENT]->(:Event)
(:Event)-[:CAUSED {explanation, evidence}]->(:Event)   // same topic
```

`Topic` (key `slug`): `name`, `display_name`, `topic_type`, `summary`, `model`, `derived`, `generated_by`,
`generated_at`. `Event` (key `(topic_slug, slug)`): `name`, `display_name`, `event_type`, `occurred_at`, `summary`,
`model`, `derived`, `generated_by`, `generated_at`. Every relationship: `derived`, `generated_by`, `generated_at`;
`CAUSED.evidence` is the list of identifiers. Constraints `topic_slug` and `event_key`.

`DERIVED_FROM` covers the whole bundle, not only cited evidence. It is what later layers use as "the sources of this
topic": the Root cause & impact layer finds a topic's PRs through it, and the Expertise layer counts a person's
activity on a topic through it.

## 8. Build behaviour

1. Require `OPENAI_API_KEY`; build the person registry from PostgreSQL (read-only).
2. Ensure the constraints.
3. **Delete first**: every relationship and then every node with `generated_by = topic-event-extraction-v1`.
   `DETACH DELETE` also removes relationships of later layers attached to topics and events
   (`docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 5).
4. For each issue: assemble the bundle, call the model, validate, write topic, `ABOUT_TOPIC`, `DERIVED_FROM`,
   events, `EVIDENCED_BY`, `ACTED_IN_EVENT`, `CAUSED`.
5. Set `PipelineState.last_layer_build_at`.

A failed call mid-run leaves a partial layer (the old one is already deleted). Run it again. No prerequisite is
checked in code, but without reference extraction the bundles contain only the issue's own nodes.

## 9. Current state (2026-09-29)

Kvitta data, built after the `cause_after_effect` rule was added. 10 topics, 51 events, 27 `CAUSED`, 388
`DERIVED_FROM`, 159 `EVIDENCED_BY`, 96 `ACTED_IN_EVENT`, 12 `ABOUT_TOPIC`; not stale. Every rule check passes (no
cross-topic or reversed `CAUSED`, all evidence inside the topic's bundle, no mailbox as actor), and every bundle
matches an independent recomputation.

| Topic (slug) | Type | Issues | Events | Bundle |
| --- | --- | --- | ---: | ---: |
| `receipt-reader-extraction` "Receipt reader extraction of amount, VAT, date, and merchant" | requirement | `KV-1`, `KV-4` | 12 | 72 |
| `mobile-app-duplicate-expense-submission` "Mobile app duplicate expense submission prevention" | defect | `KV-8`, `KV-12` | 8 | 68 |
| `fortnox-export-authentication-outage` "Fortnox export authentication outage and token-flow remediation" | incident | `KV-7` | 4 | 56 |
| `offline-expense-capture` "Offline expense capture with local queue and automatic retry" | requirement | `KV-2` | 5 | 50 |
| `approval-limits-per-manager` "Approval limits per manager" | requirement | `KV-5` | 5 | 42 |
| `kvitta-1-0-release` "Kvitta 1.0 release" | decision | `KV-9` | 3 | 28 |
| `emma-onboarding-first-tasks` "Emma Chen onboarding first tasks" | other | `KV-3` | 4 | 26 |
| `receipt-image-retention-and-finance-only-access` "Receipt image retention and finance-only access" | requirement | `KV-6` | 5 | 23 |
| `visma-export` "Visma export of approved expenses" | requirement | `KV-10` | 2 | 16 |
| `upload-spinner-slow-network` "Upload spinner remains active after slow-network receipt upload" | defect | `KV-11` | 3 | 7 |

Two topics joined two issues each: `KV-4` joined the receipt reader topic, and `KV-12` joined the duplicate topic.

Key events of the central stories:

| When | Topic | Type | Event | Evidence |
| --- | --- | --- | --- | --- |
| 2026-02-12 13:00 | offline | decided | Local queue with automatic retry decided | `seg-010`, `doc-003` |
| 2026-02-12 16:30 | duplicate | decided | Offline queue with retry was accepted despite identified duplicate risk | `doc-003`, `doc-003 v1` |
| 2026-02-20 12:00 | offline | resolved | Offline queue and server sync support delivered | `KV-2 v4`, `slack-019`, `kvitta-api#21`, `kvitta-mobile#6` |
| 2026-03-03 08:05 | Fortnox | regressed | Fortnox exports began failing with 401 Unauthorized | `KV-7 v1`, `slack-026` |
| 2026-03-04 21:45 | Fortnox | resolved | Hotfix restored Fortnox exports and drained queued expenses | `KV-7 v3`, `slack-030`, `kvitta-api#55` |
| 2026-03-09 10:00 | duplicate | created | Duplicate payout of a train-ticket expense reported | `KV-8 v1`, `slack-032` |
| 2026-03-09 10:40 | duplicate | decided | Timeout retry and missing payout deduplication identified as cause | `seg-034`, `KV-8 v2`, `comment-011`, `slack-033` |
| 2026-03-09 10:45 | offline | regressed | Offline retry contributed to a duplicate payout incident | `KV-8 v2`, `comment-011`, `doc-003` |
| 2026-03-10 10:00 | release | decided | Kvitta 1.0 scope and release blockers set | `seg-030`..`seg-032`, `KV-9 v2`, `slack-037` |
| 2026-03-12 16:00 | duplicate | other | Payout-export protection found not to prevent duplicate app submissions | `review-030`, `review-031`, `kvitta-api#60 app/payouts/export.py` |
| 2026-03-20 10:00 | release | decided | Go approved for Kvitta 1.0 | `seg-043`..`seg-045`, `comment-016`, `slack-045`, `doc-005 v3` |

`CAUSED` chains include: requirement -> decision -> delivered -> "Offline retry contributed to a duplicate payout
incident" (offline topic); "accepted despite identified duplicate risk" -> "Duplicate payout reported", and "found not
to prevent duplicate app submissions" -> "KV-12 opened" (duplicate topic); outage -> hotfix -> permanent fix
(Fortnox); scope set -> go -> released (release). The customer Anders Nyberg acts in three events (the VAT report, his VAT
confirmation and the duplicate payout report). Wording and counts
vary a little between rebuilds.

## 10. What the data needs for this layer

- **Issues are the unit.** Every storyline needs at least one issue; a PR or discussion without an issue has no topic.
- **Several distinct topics.** Issues about different subjects give different topics; related issues (a follow-up
  bug) join an existing topic. Two or more topics with events within 30 days of each other are needed for
  cross-topic causal links in layer 4.
- **Reach.** Name the issue key in the Slack messages, mails, meeting segments, reviews and documents that belong to
  its story; have the issue (versions, comments) name its PRs and documents. Put the key in at least one segment of
  each relevant meeting so the whole meeting joins.
- **Timestamps in the evidence** for every step of the story, since `occurred_at` must come from the evidence.
- **Explicit causes in the text** ("blocked until", "because the security review rejected", "this regressed after"),
  since causal links require textual support.
- **Full, distinct person names** in text and author fields, since actors are resolved by name.
- A realistic history: status changes on issue versions, rewritten requirements with `change_summary`, rejected and
  reworked PRs, and contradictions or gaps (a warning nobody followed up) give events of every type.

## 11. API

`GET /api/knowledge`: `topics` (`slug`, `name`, `topic_type`, `summary`, `event_count`, `issues`), `events`
(`topic_slug`, `slug`, `name`, `event_type`, `occurred_at`, `summary`, `evidence` = distinct display names of
`EVIDENCED_BY` targets, `actors`), `causal_links` (`topic_slug`, `cause_slug`, `cause_name`, `effect_slug`,
`effect_name`, `explanation`, `evidence`), `relationships` (per type: `relationship_type`, `from_labels`,
`to_labels`, `count`), `last_extraction_at`, `last_layer_build_at`, `needs_layer_rerun`, `stale_reasons`.

`POST /api/knowledge/build`: runs the build and returns the same plus `built_at`, `deleted_relationships`,
`deleted_nodes`, `calls`, `model`, `discarded_evidence`, `overflow_events`, `overflow_links`, `token_usage`.

`evidence` in `events` is a distinct list of display names, so the two versions of one Slack message (same display
name) show as one entry; the relationships keep both.

## 12. UI

Tab `Knowledge layer`. Button `Build knowledge layer` / `Building knowledge layer...`; status row (last reference
extraction, last knowledge build); stale warning. Description: `Uses a model to find what each issue is about, what
happened, who was involved and which events caused which, grounded in the source material.` Counts under
`Nodes and relationships:` (Topics, Events, Causal links). Run metrics (Model, Calls, Tokens, Discarded evidence) only
right after a build. Tables in order: `Topics (node)` (Topic, Type, Summary, Events, Issues), `Events (node)` (Topic,
Event, Type, Occurred at, Summary, Actors, Evidence), `Relationships (all relationship types)`, `Causal links
(relationship: CAUSED)` (Topic, Cause, Effect, Explanation, Evidence). Empty state: `No knowledge layer yet. Press the
button to build it.`

Graph filter `Knowledge`: `ABOUT_TOPIC`, `DERIVED_FROM`, `EVENT_OF_TOPIC`, `EVIDENCED_BY`, `CAUSED`,
`ACTED_IN_EVENT`.

## 13. Boundaries

- Reads and writes Neo4j; reads PostgreSQL only to build the person registry.
- Deletes only its own nodes and relationships (`generated_by`), never source data or `MENTIONS_*`.
- Everything it writes is interpretation and stays grounded through `DERIVED_FROM`, `EVIDENCED_BY` and
  `CAUSED.evidence`.
