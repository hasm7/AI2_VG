# AI2_VG

A graph-based memory system for a software team. Simulated source material (mail, Slack, Teams meetings, issues,
documents and pull requests) is stored in PostgreSQL, imported into Neo4j, enriched by seven graph layers, and made
searchable for an AI agent that answers questions in a chat with cited sources.

```text
Sources -> PostgreSQL (source of truth) -> Import into Neo4j -> 7 graph layers -> AI agent + graph view
```

## What it can answer

- who discussed a decision before it was made, and where
- which documents, messages and meetings belong to the same topic
- why something happened: the chain of causes behind an event
- which code components a change touched, and who knows them best
- how a requirement, issue or document changed over time

## The example data: Kvitta

The repository comes with the **Kvitta** scenario (`data/kvitta_seed.sql`, 263 rows, described in Swedish in
`kvitta/`). The team at Kvitta AB builds an expense app for its pilot customer Bergström & Co, across nine storylines:
the receipt reader, the VAT requirement, the offline queue, the Fortnox outage, a duplicate payout, onboarding,
approval limits, the 1.0 release and image retention.

After all layers the graph holds **542 nodes and 2 851 relationships**, and the agent answers all **27 test
questions** (`kvitta/demofrågor.md` has the ten demo questions with answer keys).

## How it works

### 1. Sources in PostgreSQL

Six logical sources in ten tables (database `hm_data`):

| Source | Tables |
| --- | --- |
| Mail | `mail_messages` |
| Slack / project chat | `slack_messages` |
| Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` |
| Issues / tickets | `issues`, `issue_versions`, `issue_comments` |
| Requirements and technical documentation | `document_versions` |
| Pull requests, code reviews and code changes | `pr_versions`, `pr_reviews` |

Every row keeps a stable ID, timestamps, versions and its source reference. The SQL viewer (port 5000) shows the data
per source and imports each source into Neo4j, merging people across sources into one `Person` each.

### 2. Seven graph layers in Neo4j

Built in this order from the `Build graph layers` tab:

| # | Layer | What it adds |
| --- | --- | --- |
| 1 | Reference extraction | Links between records that name each other (issue keys, PR numbers, document IDs) |
| 2 | Knowledge layer | `Topic` and `Event` nodes: what each issue is about and what happened, with its sources |
| 3 | Architecture layer | Repositories, components and files, and which changes touched them |
| 4 | Root cause & impact layer | Cause-and-effect links between events, in time order |
| 5 | Expertise & collaboration layer | Who knows which topic and component, who works with whom |
| 6 | Graph algorithms | Importance, bridges and groups (networkx) |
| 7 | Embeddings | Vector and fulltext indexes for search (`text-embedding-3-large`, long texts in chunks) |

Each layer shows when it needs a rebuild after the data below it changed.

### 3. The AI agent

The `Chat with AI` tab runs a LangGraph agent:

1. A **planner** reads the question and picks what to look for.
2. **Hybrid search** (vector + fulltext) finds the entry points in the graph.
3. Four **specialists** run in parallel (sources, causes, architecture, people), each collecting the most relevant
   nodes and compact facts (a topic's sources, a causal chain, the groups, the people outside the team).
4. A **check** step and an optional **explorer** complete the evidence.
5. The **answer** is streamed back with citations to the source records; the facts can be shown under each answer.

The answer's evidence is highlighted in the graph view. Models, limits and reasoning effort are set in the
`Configure AI agent` tab, which also runs the test questions and shows the agent flow.

### 4. The graph view

The left panel draws the graph with filters per layer (References, Knowledge, Architecture, Causes, Collaboration,
Algorithms, Embeddings). Click a node to see its properties; adjust the spacing with the `Distance` slider.

## Setup (first time)

Requirements: PostgreSQL 18, Neo4j (2026.x, installed through Neo4j Desktop), Node.js, and the project Python virtual
environment in `.venv`.

1. **Python dependencies** (always through the script, which uses `.venv`):

   ```powershell
   .\scripts\install_deps.ps1
   ```

2. **Frontend dependencies**:

   ```powershell
   cd frontend
   npm.cmd install
   ```

3. **Settings** in `.env` in the project root (never commit it):

   | Key | Value |
   | --- | --- |
   | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection |
   | `NEO4J_URI` | default `neo4j://127.0.0.1:7687` |
   | `NEO4J_USER` | default `neo4j` |
   | `NEO4J_PASSWORD` | required |
   | `NEO4J_DATABASE` | default `neo4j` |
   | `OPENAI_API_KEY` | for the LLM layers, embeddings and the agent |
   | `BACKEND_RELOAD` | optional, `1` turns on Flask's auto-reloader |

4. **Database schema and example data**:

   ```powershell
   .\.venv\Scripts\python.exe scripts\setup_postgres_schema.py
   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h localhost -U <user> -d hm_data -f data\kvitta_seed.sql
   ```

5. **Build the graph**: start the app (below), open the SQL viewer with `Open SQL Viewer`, import the six sources,
   then build the seven layers in order in the `Build graph layers` tab.

## Starting the program

Start the three parts in this order.

**1. Neo4j** (runs in the background, Bolt on 7687, browser on 7474):

```powershell
.\scripts\run_neo4j.ps1
```

**2. Backend** (Flask API on port 8000):

```powershell
.\scripts\run_backend.ps1
```

**3. Frontend** (React + Vite on port 5173), in a second terminal:

```powershell
cd frontend
npm.cmd run dev -- --host 127.0.0.1
```

Then open **http://127.0.0.1:5173**.

Steps 2 and 3 can also be started together:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_app.ps1
```

The SQL viewer (port 5000) opens from the app's `Open SQL Viewer` button, or with `.\scripts\run_viewer.ps1`.
Stop Neo4j with `.\scripts\stop_neo4j.ps1`. `npm.cmd run build` in `frontend` builds the frontend for a server.

## Documentation

| Document | Content |
| --- | --- |
| `AGENTS.md` | Rules for coding agents: venv, scripts, database access, language |
| `kvitta/` | The Kvitta scenario (Swedish): product, storylines, team and systems, demo questions |
| `docs/SESSION_HANDOFF.md` | Current state, how to run and work in the repository |
| `docs/SQL_DATA_HANDOFF.md` | Sources, tables, columns, identity and reference rules, import |
| `docs/DATA_GENERATION_GUIDE.md` | What a new dataset must contain; validation queries |
| `docs/GRAPH_DATA_HANDOFF.md` | Neo4j labels, relationships, constraints, indexes, graph API and panel |
| `docs/PIPELINE_AND_LINKS_HANDOFF.md` | Build order, staleness, how everything links |
| `docs/*_LAYER_HANDOFF.md`, `docs/GRAPH_ALGORITHMS_HANDOFF.md` | One document per layer |
| `docs/AI_AGENT_HANDOFF.md` | The chat agent: flow, specialists, facts, settings, test questions |
| `docs/SCALING_HANDOFF.md` | How to grow to much larger data |

## Project structure

```text
backend/            Flask API (port 8000), graph layers, AI agent (backend/ai_agent/)
frontend/           React + Vite app (port 5173)
viewer/             SQL viewer and SQL-to-Neo4j import (port 5000)
scripts/            setup and start scripts
data/               example data (kvitta_seed.sql)
kvitta/             the Kvitta scenario, in Swedish
docs/               documentation
backups/            earlier datasets
draft/              early planning sketches
AGENTS.md           rules for coding agents
requirements.txt    Python dependencies
```
