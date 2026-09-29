# Layer 5: Expertise & Collaboration Layer

The fifth graph-building step (code, API and graph filter use the name `collaboration`). Fully deterministic, no
model. From activity already in the graph it computes **expertise** (how much each person has done on each topic and
component, with the activity it rests on) and **collaboration** (which pairs of people share work items, and how
many).

**Verified on 2026-09-29** against `backend/collaboration_layer.py`, `backend/app.py`, `frontend/src/main.tsx` and the
live graph.

Code: `backend/collaboration_layer.py`. UI: `CollaborationLayerPanel`, tab `Expertise & collaboration layer` (fifth of
seven).

## 1. Eligible persons

Every `Person` except `actor_type = "mailbox"` and `identity_ambiguous = true`. Excluded persons get no expertise and
no `WORKS_WITH`, and are returned by the API with their reason (`mailbox`, `ambiguous identity`). The same rule is used
by layers 6 and 7 and by the agent.

## 2. Activities and weights

An activity is a node an eligible person is linked to by one of these relationships:

| Relationship from `Person` | Target | Weight |
| --- | --- | ---: |
| `AUTHORED_PR` | `PullRequest` | 3 |
| `WROTE_PR_REVIEW` | `PullRequestReview` | 2 |
| `AUTHORED_DOCUMENT_VERSION` | `DocumentVersion` | 2 |
| `ACTED_IN_EVENT` | `Event` | 2 |
| `CHANGED_ISSUE_VERSION` | `IssueVersion` | 1 |
| `WROTE_ISSUE_COMMENT` | `IssueComment` | 1 |
| `SENT_SLACK_MESSAGE` | `SlackMessage` | 1 (every version counts) |
| `SENT_MAIL` | `MailMessage` | 1 |
| `SPOKE_TEAMS_TRANSCRIPT_SEGMENT` | `TeamsTranscriptSegment` | 1 |

Not activities: receiving mail, taking part in a meeting without speaking, owning or creating an issue, reviewing
(`REVIEWED_PR`; the review entries themselves count), `AUTHORED_DOCUMENT` (the versions count).

Activity time: the first of `occurred_at`, `version_at`, `sent_at`, `created_at`, `started_at` present on the node; a
segment uses its meeting's `started_at`.

## 3. Subjects

A subject is a `Topic` or a `Component`. An activity node belongs to:

- **Topic T** if `(T)-[:DERIVED_FROM]->(node)` (the node was in T's evidence bundle), or the node is an `Event` of T.
- **Component C** if the node is a `PullRequest` whose code changes modify a file C is implemented in, or a
  `PullRequestReview` of such a PR, or `(C)-[:COMPONENT_EVIDENCED_BY]->(node)`, or an `Event` with
  `AFFECTED_COMPONENT` to C.

A node counts once per person and subject even if several rules match.

## 4. Expertise

For each (person, subject): `score` = sum of the weights of the distinct activity nodes; entries with score below
`MIN_EXPERTISE_SCORE = 2` are not written. Per subject, over the written entries: `share = score / total`, rounded to
3 decimals; `rank` 1 = highest score, ties broken by `person_key`. Also `activity_count`, `first_activity_at`,
`last_activity_at` (text-sorted timestamps).

```cypher
(:Person)-[:HAS_EXPERTISE]->(:Expertise)
(:Expertise)-[:EXPERTISE_IN]->(:Topic | :Component)
(:Expertise)-[:EXPERTISE_EVIDENCED_BY]->(:ActivityNode)   // every activity counted
```

`Expertise` key `(person_key, subject_label, subject_key)` (constraint `expertise_key`); `subject_key` is the topic
slug or `<source_instance>/<repository>/<component slug>`; `display_name` `<person name> – <subject name>`.

## 5. Collaboration (`WORKS_WITH`)

Work items and who is on them (eligible persons only):

| Work item | People |
| --- | --- |
| `Issue` | `CREATED_ISSUE`, `OWNS_ISSUE`, `COMMENTED_ON_ISSUE`, and `CHANGED_ISSUE_VERSION` on any of its versions |
| `PullRequest` | `AUTHORED_PR`, `REVIEWED_PR` |
| `TeamsMeeting` | `PARTICIPATED_IN_MEETING` (participants and speakers) |
| `MailMessage` | sender and every recipient; **each mail is its own work item**, not the thread |
| `Event` | `ACTED_IN_EVENT` |

Every pair on the same work item gets +1. One `WORKS_WITH` per pair, written from the smaller `person_key` to the
larger (the meaning is undirected), with `weight` (number of shared work items), `shared_work_items` (display names,
at most 50) and `work_item_types` (sorted labels).

## 6. Build behaviour

Prerequisites: `last_layer_build_at`, `last_architecture_build_at`, `last_causal_build_at` (else 409 `Run Knowledge,
Architecture and Root cause & impact layers first.`). No model, never 503. Compute everything, delete this layer's
nodes and relationships (`generated_by = collaboration-layer-v1`), write `Expertise`, `HAS_EXPERTISE`,
`EXPERTISE_IN`, `EXPERTISE_EVIDENCED_BY`, `WORKS_WITH`, set `last_collaboration_build_at`. All derived items carry
`derived`, `generated_by`, `generated_at`.

## 7. Current state (2026-09-29)

Kvitta data. 109 expertise entries (580 `EXPERTISE_EVIDENCED_BY`), 8 persons with expertise, 24 pairs, 2 excluded
(Kvitta Support and Kvitta Alerts, both mailboxes); 109 nodes and 822 relationships; not stale. Every score, share,
rank, activity count, date, evidence list and pair weight matched an independent recomputation.

| Person | Entries | Total score | Rank 1 in |
| --- | ---: | ---: | --- |
| Ahmed Karimi | 21 | 222 | 6 (Fortnox topic and the three Fortnox components, Payout export, Offline expense sync endpoint) |
| Maria Lindgren | 16 | 172 | 2 (Kvitta 1.0 release, Visma export) |
| David Okafor | 19 | 163 | 1 (Receipt image retention topic) |
| Nina Petrova | 18 | 123 | 2 (duplicate payout topic, upload spinner topic) |
| Sofia Berg | 11 | 121 | 5 (receipt reader topic, Receipt reader, Receipt VAT rules, Receipt image storage, Expense upload API) |
| Emma Chen | 10 | 107 | 7 (approval limits topic and components, her onboarding topic) |
| Lucas Holm | 9 | 84 | 5 (offline topic, Offline expense queue, Expense upload, Retry delivery, Expense sync endpoint) |
| Anders Nyberg (customer) | 5 | 13 | 0 |

Selected subjects (score, share):

| Subject | Experts |
| --- | --- |
| Component Fortnox | Ahmed 8 (1.0) |
| Component Fortnox authentication | Ahmed 14 (0.636), David 6 (0.273), Sofia 2 (0.091) |
| Component Receipt VAT rules | Sofia 11 (0.524), David 8 (0.381), Nina 2 (0.095) |
| Component Receipt image storage and retention | Sofia 13 (0.52), David 10 (0.4), Maria 2 (0.08) |
| Component Receipt reader | Sofia 18 (0.45), David 12 (0.3), Maria 4, Nina 4, Anders 2 |
| Topic Receipt reader extraction | Sofia 42 (0.356), Maria 37 (0.314), David 19, Nina 7, Anders 5, Ahmed 3, Emma 3, Lucas 2 |
| Topic Mobile app duplicate expense submission prevention | Nina 27 (0.273), Ahmed 23, Maria 22, David 16, Lucas 9, Anders 2 |

`WORKS_WITH` (weight): Ahmed–David 23, David–Sofia 23, Ahmed–Nina 20, Ahmed–Emma 18, David–Maria 18, David–Nina 18,
Ahmed–Maria 15, Lucas–Nina 15, Ahmed–Lucas 14, Anders–Maria 13, David–Lucas 13, Maria–Nina 13, Maria–Sofia 13, and 11
smaller pairs down to Anders–Sofia 1. All 21 pairs of the seven team members are linked, mostly through shared
meetings and the team-wide mail `mail-004`. The customer Anders is linked to Maria (13), David (2) and Sofia (1). His
expertise comes from the three events he acts in (the VAT report, his VAT confirmation, the duplicate payout report)
and from `mail-005`, the only one of his mails inside a topic bundle. Values change when
the LLM layers are rebuilt.

## 8. What the data needs for this layer

- **More people with different roles** (developers, a reviewer, a product owner, QA, operations, customers), each
  active in several sources with one resolvable identity (`docs/SQL_DATA_HANDOFF.md`, section 7).
- **Uneven expertise**: one person who does almost all work on one component (bus factor 1 in layer 6), shared work on
  another, a newcomer with little activity.
- **Groups that work mostly together**, with a few people bridging groups (for communities and betweenness in layer
  6): for example a backend group and a mobile group sharing one reviewer.
- **Outsiders** (customer contacts) who only mail, and a shared mailbox, to show how they are treated.
- Activities inside topic bundles (they name the issue), so they count toward topic expertise.

## 9. API

`GET /api/collaboration`: pipeline timestamps (`last_import_at`, `last_layer_build_at`,
`last_architecture_build_at`, `last_causal_build_at`, `last_collaboration_build_at`), `needs_rerun`, `stale_reasons`,
`counts` (`expertise`, `persons_with_expertise`, `works_with_pairs`, `excluded_persons`), `expertise` (per row:
`person_name`, `subject_label`, `subject_name`, `score`, `share`, `rank`, `activity_count`, `first_activity_at`,
`last_activity_at`, `evidence`; Slack evidence gets a ` v<n>` suffix), `works_with`, `excluded_persons`,
`relationships`.

`POST /api/collaboration/build`: the same plus `built_at`, `deleted_relationships`, `deleted_nodes`. Errors 409, 500.

## 10. UI

Tab `Expertise & collaboration layer`. Button `Build expertise & collaboration layer` / `Building expertise &
collaboration layer...`. Description: `Scores each person's expertise per topic and component from their activity,
and finds which people share work items.` Counts under `Nodes and relationships:` and `People (existing Person
nodes):`. Tables: `Expertise (node)` (with an Evidence column), `Relationships (all relationship types)`,
`Collaboration (relationship: WORKS_WITH)`, `Excluded persons (existing Person nodes)`.

Graph filter `Collaboration` (button `Expertise`): `HAS_EXPERTISE`, `EXPERTISE_IN`, `EXPERTISE_EVIDENCED_BY`,
`WORKS_WITH`.

## 11. Boundaries

- Reads and writes Neo4j only; no model; never creates `Person` nodes.
- Deletes only by `generated_by = collaboration-layer-v1`.
- Depends on layers 2, 3 and 4 for subject membership.
