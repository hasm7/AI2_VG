# Decided Project Direction

## Project Context and Goal

This project simulates source material from a software engineering team working on a shared product.

The material should describe the same project, people, events, needs, decisions, issues, documents, and implementation work across time. Each source should contribute a different part of the context so that a memory system can later find relationships between requirements, discussions, decisions, tickets, documentation, and code changes.

The goal is to make it possible to find context and relationships across multiple sources, for example:

- who discussed a decision before it was made
- which documents describe the background to a requirement
- which source entries refer to the same event
- which people, issues, meetings, and code changes belong together
- where information is missing, changed, or contradictory

## The Six Data Sources and Their Nine SQL Tables

PostgreSQL preserves the original source material before graph import. Source IDs, timestamps, versions, and source references are retained so the graph layer can build relationships while keeping the original context traceable.

The full PostgreSQL schema, including columns, data types, keys, constraints, indexes, and table relationships, is documented in `docs/SQL_DATA_HANDOFF.md` and technically defined in `scripts/setup_postgres_schema.py`.

### 1. Mail

Email threads between customers, product owners, and the team.

This source contains needs, clarifications, and decisions communicated between parties.

**Table: `mail_messages`**

One row contains one email message with sender, recipients, subject, body, timestamp, and reply reference. Recipients are stored as a structured JSONB array.

### 2. Slack / Project Chat

The team's ongoing project communication in channels, messages, and threads.

This source contains questions, discussions, informal agreements, and day-to-day coordination.

**Table: `slack_messages`**

One row contains one version of one Slack message with author, channel, body, timestamp, and thread reference. Earlier message versions are preserved when messages are edited.

### 3. Teams / Meeting Transcripts

Meeting conversations stored as text, including speaker and timing information.

This source contains spoken discussions, tradeoffs, and decisions.

**Table: `teams_meetings`**

One row contains meeting metadata: source instance, meeting ID, title, start/end times, participants, and source URL.

**Table: `teams_transcript_segments`**

One row contains one ordered transcript segment with meeting ID, speaker, text, offsets, and sequence number.

### 4. Issues / Tickets

Work items and bugs with owner, comments, acceptance criteria, status, and history.

Issues connect needs to concrete work.

**Table: `issue_versions`**

One row contains the currently allowed stored issue row for one issue in the live database, including description, acceptance criteria, status, owner, timestamps, and source URL.

Important live database note: the table contains `version_number`, but it also has a unique constraint on `(source_instance, issue_id)`. That means the current database only allows one row per issue ID unless the schema is changed.

**Table: `issue_comments`**

One row contains one version of one issue comment with issue ID, author, body, timestamps, optional reply reference, and source URL.

### 5. Requirements and Technical Documentation

Documents that describe what the product should do and how the solution is intended to work.

Earlier document versions are preserved so changes to requirements and technical design can be followed over time.

**Table: `document_versions`**

One row contains one version of one document with document ID, type, title, body, author, version, timestamps, change summary, and source URL.

### 6. Pull Requests, Code Reviews, and Code Changes

Pull requests with descriptions of proposed changes, review comments, review decisions, and code-change metadata.

This makes it possible to compare requirements, tickets, PR descriptions, reviews, and implementation details.

**Table: `pr_versions`**

One row contains one version of one pull request with repository, PR number, title, description, state, commits, code changes, timestamps, and source URL. Code changes are stored as a structured JSONB array.

**Table: `pr_reviews`**

One row contains one version of one PR review entry, such as a comment, approval, change request, or line-specific code comment. File and line metadata are present when the entry refers to a specific code location.

## Code Material Scope

Code material should be limited to selected functions or files with a coherent change history. The code examples should provide enough context to compare what a PR says with what the code change actually shows.

Reviews and comments should be linkable to the PR version and commit they refer to.

## Relationships and History

The material should connect across sources. A need may be expressed in an email, discussed in Slack and in a meeting, documented as a requirement, tracked as an issue, and implemented in a PR.

The sources may contain different perspectives, changed decisions, gaps, and contradictions. Historical versions should be preserved where the database schema allows it, so development can be followed over time.

## Storage and Usage

The SQL layer preserves simulated source material in source-oriented form and acts as the basis for import into the graph database.

The graph database acts as a memory system. Agents use it to find context and analyze the material. Source content and traceable references should be kept with the objects and relationships extracted from the SQL layer.

The flow is:

```text
Simulated sources -> SQL original material -> Extraction and transfer
-> Graph database / memory system -> Agent analysis
```

## Work Order

The memory system is built first: storage, transfer, relationships, and context search. Deeper reasoning and the full multi-agent system come later.
