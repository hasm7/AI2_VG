# Data Generation Guide

For whoever generates the next example dataset. It says what the data must contain so that **every graph layer and
the AI agent have something real to find**, and how to check the data before it is loaded. It does not repeat the
table reference: read `docs/SQL_DATA_HANDOFF.md` first (tables, columns, constraints, JSON shapes, identity and
reference rules). Every rule below comes from what the code does, verified on 2026-09-29.

Reading order: this guide, `docs/SQL_DATA_HANDOFF.md`, `docs/PIPELINE_AND_LINKS_HANDOFF.md`, then the layer documents
(`REFERENCE_EXTRACTION`, `KNOWLEDGE`, `ARCHITECTURE`, `CAUSAL`, `COLLABORATION`, `GRAPH_ALGORITHMS`, `EMBEDDING`
`_LAYER_HANDOFF.md`), each of which ends with a section "What the data needs for this layer".

## 1. What to deliver

- **SQL `INSERT` statements** for the ten existing tables, in foreign-key order (`mail_messages`, `slack_messages`,
  `teams_meetings`, `teams_transcript_segments`, `issues`, `issue_versions`, `issue_comments`, `document_versions`,
  `pr_versions`, `pr_reviews`), with named columns, wrapped in one transaction (`BEGIN; ... COMMIT;`) so a single
  error loads nothing.
- **No schema changes**: no new tables, columns, types or constraints. The layers read exactly the columns that exist.
- Every row must pass every primary key, unique, foreign key and check constraint (`docs/SQL_DATA_HANDOFF.md`, per
  table). JSONB fields as real JSON arrays (`'[...]'::jsonb`), timestamps with offsets.
- English text (the agent translates questions into English keywords for fulltext search).
- A short summary of the storylines, the people and their roles, and for each storyline the facts a good answer should
  contain. The agent's test questions will be rewritten from it.

**Replace or extend?** The dataset can either replace today's (then the tables are emptied or recreated and the graph
is emptied before import) or extend it (keep the `AUTH-17` story and add more, with IDs that do not collide). That is
the user's decision; ask before assuming. Loading and rebuilding are done by the user
(`docs/PIPELINE_AND_LINKS_HANDOFF.md`, section 6).

## 2. Size

Today: 64 rows, 108 graph nodes, 6 people. The goal is enough data to show every layer working, without reaching the
limits in `docs/SCALING_HANDOFF.md` (the agent and the graph panel are built for a few hundred nodes; thousands need
changes first).

| Item | Suggested | Why |
| --- | --- | --- |
| People | 10 to 14 team members, 2 to 3 customer contacts, 1 to 2 shared mailboxes | communities, betweenness, bus factor need a real network |
| Repositories | 3 (for example `backend-api`, `mobile-app`, `web-portal` or `shared-lib`) | several component graphs |
| Issues | 8 to 12, each with 2 to 6 versions | topics are built per issue |
| Topics (storylines) | 3 to 5, some related to each other | cross-topic causal links |
| Pull requests | 10 to 15, several with 2 to 4 versions | code history, reviews, components |
| Meetings | 4 to 6, 5 to 15 segments each | decisions made in speech |
| Slack | 50 to 90 rows, several channels, threads, a few edited messages | informal agreements and warnings |
| Mail | 10 to 20 in a few threads | customer reports and replies |
| Documents | 6 to 10, most with 2 or more versions; 2 to 3 `technical-design` | requirements that change; design |

That gives roughly 300 to 500 graph nodes, which the current code handles without changes. The LLM layers make one
call per issue, per repository and per topic, so a full build stays cheap.

Known limit at this size: with more than 8 eligible people, "who has the most ..." answers compare only the profiles
the agent fetched (`docs/SCALING_HANDOFF.md`). Worth a test question, not a reason to stay small.

## 3. Storylines

The project's purpose is to find context and relationships across sources:

- who discussed a decision before it was made,
- which documents describe the background to a requirement,
- which source entries describe the same event,
- which people, issues, meetings and code changes belong together,
- where information is missing, changed or contradictory.

Write storylines that make each of these questions answerable, and some where the answer is "the sources disagree" or
"nobody followed up". Each storyline should run across **all six sources**: a need raised in mail or chat, discussed
in Slack and a meeting, written down as a requirement document, tracked as an issue with a status history,
implemented in one or more PRs with reviews, and followed up.

Make the storylines **connect**: a follow-up bug on an earlier story (same topic), an incident in one area that delays
work in another (cross-topic cause, within 30 days), a shared component touched by several stories, a person active in
several stories, a reviewer who spans teams.

Include realistic friction: a rejected first approach, a requirement rewritten, a ticket blocked and resumed, a
reviewer requesting changes, a warning in a meeting that nobody acts on, a PR description that claims more than its
code change does, two documents that contradict each other, a decision made in a meeting before the ticket was
updated.

## 4. Checklist per layer

### Import and identity (`docs/SQL_DATA_HANDOFF.md`, section 7)

- [ ] Each team member: one source ID used in every system, one work email, and at least one Slack row or Teams
      participant entry carrying **both** (otherwise their mail identity and their ticket/PR identity become two
      people).
- [ ] Full names, used consistently (display names may vary in casing or a short form in some rows).
- [ ] Customers with their own email domain (they appear only in mail).
- [ ] At least one shared mailbox (`support@`, `alerts@`, `noreply@`, ...): kept, but excluded from people analysis.
- [ ] Optionally one deliberate ambiguity: a speaker or name-only mention of a first name shared by two people (like
      today's `Anna`), to show that the system refuses to guess.
- [ ] Optionally one person with a source ID but no email anywhere (like today's Priya Raman).

### Layer 1, references (`docs/REFERENCE_EXTRACTION_LAYER_HANDOFF.md`)

- [ ] Issue keys `PROJ-123` (uppercase prefix), PR references `repo-name#123` (lowercase repository), document
      identifiers from titles `IDENT: Title`.
- [ ] Every Slack message, mail, segment, review, comment and document that belongs to a storyline names at least one
      issue, PR or document, as a person would ("PAY-12 is blocked on web-portal#8", "see REQ-BILLING-EXPORT v2").
- [ ] Issues name their PRs and documents (description or comments); PRs name their issues (title `PAY-12: ...`).
- [ ] Some cross-issue and cross-PR mentions.
- [ ] Some messages that name nothing (small talk, a vague warning): realistic, reachable only by search.

### Layer 2, knowledge (`docs/KNOWLEDGE_LAYER_HANDOFF.md`)

- [ ] Every storyline has an issue; related issues (follow-ups) and unrelated ones (different topics).
- [ ] The issue key appears in at least one segment of each meeting about it (the whole meeting then joins the
      evidence).
- [ ] Explicit causal language in the text ("because", "blocked until", "after X we saw Y").
- [ ] Timestamps for every step (events take their time from the evidence).

### Layer 3, architecture (`docs/ARCHITECTURE_LAYER_HANDOFF.md`)

- [ ] Realistic file paths in several directories per repository, reused across PRs (some files changed many times).
- [ ] `before_summary` / `after_summary` that describe behaviour; diffs short and plausible.
- [ ] `technical-design` documents describing components and how they call each other.
- [ ] Text that states dependencies ("the export job reads from the ledger store", "the mobile client calls the shared
      token service").
- [ ] Test and config files too (they get files, usually no component).

### Layer 4, root cause & impact (`docs/CAUSAL_LAYER_HANDOFF.md`)

- [ ] Each issue linked to its PRs (so code and components are in reach of its topic).
- [ ] Underlying reasons stated or clearly implied: requirement gap, design decision, partial implementation, missing
      test, process failure, external dependency.
- [ ] At least two topics with events within 30 days of each other, where one caused the other, and text that says so.

### Layer 5 and 6, expertise, collaboration, algorithms (`docs/COLLABORATION_LAYER_HANDOFF.md`, `docs/GRAPH_ALGORITHMS_HANDOFF.md`)

- [ ] Two or three groups who mostly work together, and one or two people who bridge them.
- [ ] One component nearly owned by a single person (bus factor 1); one with spread knowledge.
- [ ] A newcomer with little activity; a very active lead.
- [ ] Authored PRs, reviews, document versions, issue changes and comments spread realistically (they are weighted
      3, 2, 2, 1, 1).

### Layer 7, embeddings (`docs/EMBEDDING_LAYER_HANDOFF.md`)

- [ ] One or two very long texts (a requirement or design document body well over 12 000 characters) to exercise
      chunking.
- [ ] Several versions of messages, issues, documents and code changes (version grouping).

## 5. IDs, time and text

- Keep the ID patterns and make them unique across the whole dataset (`docs/SQL_DATA_HANDOFF.md`, section 10):
  `mail-001`, `slack-001`, `meet-001`, `seg-001` (continue across meetings), `issue-001`, `comment-001`, `doc-001`,
  `review-001` (continue across PRs), `rg-001`. Zero-pad.
- Keep one `source_instance` per system: `gmail-main`, `slack-main`, `teams-main`, `jira-main`, `docs-main`,
  `github-main` (or others, used consistently).
- If extending today's data, start numbering after the existing IDs (`mail-005`, `slack-012`, `seg-010`,
  `issue-003`, `comment-006`, `doc-003`, `review-007`, PR numbers above 47 or other repositories) and do not reuse the
  issue keys `AUTH-17`, `AUTH-19` or the identifier `REQ-AUTH-SESSION` for new things.
- Timestamps with offsets, in a plausible working rhythm (working hours, weekdays). Keep related items in order. The
  database returns times in `Europe/Berlin`; to avoid mixed offsets, keep the whole dataset between two daylight
  saving changes (for example January to mid-March 2026, all `+01:00`), or accept `+02:00` after the last Sunday of
  March.
- Commits: short hex strings (`a1b2c3d4`), a new `head_commit` per PR version, reviews citing the reviewed version's
  `head_commit`.
- URLs: consistent fake hosts (`https://jira.example.com/browse/PAY-12`, ...).

## 6. Checks to run after loading (read-only SQL)

Each query should return **no rows**, except the two marked "inspect" and "review" and the rough check 13. All of them
were run read-only against today's data on 2026-09-29: they return only `u-priya` (1, intentional: no email
anywhere), `doc-002` (5b, a design document without an identifier) and eight rows in check 13 (small talk, customer
mails, and meeting lines that rely on the rest of their meeting); everything else is empty.

```sql
-- 1. Source IDs used in issues, docs, PRs or transcripts that never appear together with an email
--    (in Slack or a Teams participant): those people will be split into two Person nodes.
WITH used AS (
    SELECT creator_source_id AS s FROM issues
    UNION SELECT assignee_source_id FROM issue_versions
    UNION SELECT changed_by_id FROM issue_versions
    UNION SELECT author_source_id FROM issue_comments
    UNION SELECT author_source_id FROM document_versions
    UNION SELECT author_source_id FROM pr_versions
    UNION SELECT author_source_id FROM pr_reviews
    UNION SELECT speaker_source_id FROM teams_transcript_segments
), bridged AS (
    SELECT author_source_id AS s FROM slack_messages WHERE author_email IS NOT NULL
    UNION SELECT COALESCE(p->>'source_id', p->>'id') FROM teams_meetings, jsonb_array_elements(participants) p
          WHERE COALESCE(p->>'email', p->>'address') IS NOT NULL
)
SELECT s FROM used WHERE s IS NOT NULL AND s NOT IN (SELECT s FROM bridged WHERE s IS NOT NULL);
-- Intentional exceptions (a person with no email at all) are fine; list them in the summary.

-- 2. Slack replies whose thread root is missing, in another channel, or not sent earlier.
SELECT r.message_id, r.thread_root_id
FROM slack_messages r
LEFT JOIN slack_messages root
  ON root.source_instance = r.source_instance AND root.workspace_id = r.workspace_id
 AND root.channel_id = r.channel_id AND root.message_id = r.thread_root_id AND root.version_number = 1
WHERE r.thread_root_id IS NOT NULL AND r.thread_root_id <> r.message_id
  AND (root.message_id IS NULL OR root.sent_at >= r.sent_at);

-- 3. Mail replies whose parent is missing or later.
SELECT m.message_id, m.in_reply_to_id
FROM mail_messages m
LEFT JOIN mail_messages p ON p.source_instance = m.source_instance AND p.message_id = m.in_reply_to_id
WHERE m.in_reply_to_id IS NOT NULL AND (p.message_id IS NULL OR p.sent_at > m.sent_at);

-- 4. Comment and review replies whose parent is missing or has a higher ID (the reply edge would be missed).
SELECT c.comment_id, c.reply_to_comment_id
FROM issue_comments c
LEFT JOIN issue_comments p ON p.source_instance = c.source_instance AND p.comment_id = c.reply_to_comment_id
WHERE c.reply_to_comment_id IS NOT NULL AND (p.comment_id IS NULL OR p.comment_id >= c.comment_id);

SELECT r.repository, r.pr_number, r.source_id, r.reply_to_source_id
FROM pr_reviews r
LEFT JOIN pr_reviews p ON p.source_instance = r.source_instance AND p.repository = r.repository
 AND p.pr_number = r.pr_number AND p.source_id = r.reply_to_source_id
WHERE r.reply_to_source_id IS NOT NULL AND (p.source_id IS NULL OR p.source_id >= r.source_id);

-- 5a (inspect). The latest version of every document: its author must be set and resolvable,
--    or no DocumentVersion nodes are created for that document.
SELECT DISTINCT ON (source_instance, document_id) document_id, version_number, author_source_id, author_name, title
FROM document_versions
ORDER BY source_instance, document_id, version_number DESC;

-- 5b (review). Documents without a referenceable identifier in the latest title; some may be intentional.
SELECT DISTINCT ON (source_instance, document_id) document_id, title
FROM document_versions
WHERE title !~ '^\s*[A-Z][A-Z0-9-]{3,}\s*:'
ORDER BY source_instance, document_id, version_number DESC;

-- 6. Creation times that differ between versions of the same object.
SELECT 'document', document_id FROM document_versions GROUP BY source_instance, document_id HAVING count(DISTINCT created_at) > 1
UNION ALL SELECT 'pr', repository || '#' || pr_number FROM pr_versions GROUP BY source_instance, repository, pr_number HAVING count(DISTINCT created_at) > 1
UNION ALL SELECT 'slack', message_id FROM slack_messages GROUP BY source_instance, workspace_id, channel_id, message_id HAVING count(DISTINCT sent_at) > 1
UNION ALL SELECT 'comment', comment_id FROM issue_comments GROUP BY source_instance, comment_id HAVING count(DISTINCT created_at) > 1;

-- 7. Version numbers with gaps.
SELECT 'issue', issue_id FROM issue_versions GROUP BY source_instance, issue_id HAVING max(version_number) <> count(*)
UNION ALL SELECT 'document', document_id FROM document_versions GROUP BY source_instance, document_id HAVING max(version_number) <> count(*)
UNION ALL SELECT 'pr', repository || '#' || pr_number FROM pr_versions GROUP BY source_instance, repository, pr_number HAVING max(version_number) <> count(*);

-- 8. Issue version 1 that does not match the issue's creation.
SELECT i.issue_key FROM issues i
JOIN issue_versions v ON v.source_instance = i.source_instance AND v.issue_id = i.issue_id AND v.version_number = 1
WHERE v.version_at <> i.created_at;

-- 9. Reviews whose commit is not the reviewed version's head, and line comments on files not in that version.
SELECT r.repository, r.pr_number, r.source_id
FROM pr_reviews r
JOIN pr_versions v ON v.source_instance = r.source_instance AND v.repository = r.repository
 AND v.pr_number = r.pr_number AND v.version_number = r.pr_version_number
WHERE (r.reviewed_commit IS NOT NULL AND r.reviewed_commit <> v.head_commit)
   OR (r.file_path IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(v.code_changes) c WHERE c->>'file_path' = r.file_path));

-- 10. Code change elements without a file path, and duplicate paths within one version.
SELECT repository, pr_number, version_number FROM pr_versions, jsonb_array_elements(code_changes) c
WHERE coalesce(c->>'file_path', '') = '';

SELECT repository, pr_number, version_number, c->>'file_path' FROM pr_versions, jsonb_array_elements(code_changes) c
GROUP BY repository, pr_number, version_number, c->>'file_path' HAVING count(*) > 1;

-- 11. Identifiers that collide across parents (the LLM layers would keep only one of them).
SELECT segment_id FROM teams_transcript_segments GROUP BY segment_id HAVING count(DISTINCT meeting_id) > 1;
SELECT source_id FROM pr_reviews GROUP BY source_id HAVING count(DISTINCT repository || '#' || pr_number) > 1;
SELECT issue_key FROM issues GROUP BY issue_key HAVING count(*) > 1;
SELECT repository, pr_number FROM pr_versions GROUP BY repository, pr_number HAVING count(DISTINCT source_instance) > 1;

-- 12. Repositories that text can never reference (must be lowercase).
SELECT DISTINCT repository FROM pr_versions WHERE repository !~ '^[a-z0-9][a-z0-9._-]*$';

-- 13. Rough check: chat, mail and transcript rows that name no issue key or PR reference
--     (document identifiers are not detected here). A few are fine; many mean isolated sources.
SELECT 'slack', message_id FROM slack_messages
WHERE body !~ '[A-Z][A-Z0-9]*-[0-9]+' AND body !~ '[a-z0-9][a-z0-9._-]*#[0-9]+'
UNION ALL SELECT 'mail', message_id FROM mail_messages
WHERE coalesce(subject, '') || ' ' || body !~ '[A-Z][A-Z0-9]*-[0-9]+'
  AND coalesce(subject, '') || ' ' || body !~ '[a-z0-9][a-z0-9._-]*#[0-9]+'
UNION ALL SELECT 'segment', segment_id FROM teams_transcript_segments
WHERE body !~ '[A-Z][A-Z0-9]*-[0-9]+' AND body !~ '[a-z0-9][a-z0-9._-]*#[0-9]+';
```

## 7. After loading

The user empties the graph, imports the six sources, builds the seven layers, checks that nothing is stale, and then
rewrites `backend/ai_agent/test_questions.json` from the new graph (`docs/AI_AGENT_HANDOFF.md`, section 9). Useful
graph checks after the build:

- every intended person is one `Person` (`MATCH (p:Person) RETURN p.name, p.person_key, p.emails, p.source_ids`);
- every storyline's issue has a topic, and the topics are the intended ones;
- `MENTIONS_*` counts per source label look as intended (Reference extraction tab);
- components per repository are plausible, and some `DEPENDS_ON` exist;
- `CROSS_TOPIC_CAUSED` is not zero if cross-topic causes were written in;
- bus factor 1 appears where intended; communities match the intended groups;
- the Embeddings tab shows chunks if long texts were included.
