# AI2_VG

This project models simulated source material from a software engineering team and builds a graph-based memory
system on top of it.

The material describes the same project, people, events, needs, decisions, issues, documents and implementation work
across time. Each source contributes a different part of the context. PostgreSQL preserves the source material with
stable IDs, timestamps, versions and source references. Neo4j holds the same material as a graph, enriched by seven
layers, and an AI agent answers questions over it.

## Goals

Find context and relationships across sources, for example:

- who discussed a decision before it was made
- which documents describe the background to a requirement
- which source entries refer to the same event
- which people, issues, meetings and code changes belong together
- where information is missing, changed or contradictory

The flow is:

```text
Simulated sources -> SQL source material -> Import and graph layers -> Graph memory system -> AI agent
```

## Data Sources and SQL Tables

Six logical sources, stored in ten tables:

| Source | Tables |
| --- | --- |
| Mail | `mail_messages` |
| Slack / project chat | `slack_messages` |
| Teams / meeting transcripts | `teams_meetings`, `teams_transcript_segments` |
| Issues / tickets | `issues`, `issue_versions`, `issue_comments` |
| Requirements and technical documentation | `document_versions` |
| Pull requests, code reviews and code changes | `pr_versions`, `pr_reviews` |

Mail recipients, meeting participants and PR code changes are `JSONB` arrays. The schema is defined in
`scripts/setup_postgres_schema.py` and documented in full in `docs/SQL_DATA_HANDOFF.md`.

## Graph Layers

Built in this order from the `Build graph layers` tab: Reference extraction, Knowledge layer, Architecture layer,
Root cause & impact layer, Expertise & collaboration layer, Graph algorithms, Embeddings. See
`docs/PIPELINE_AND_LINKS_HANDOFF.md`.

## Documentation

| Document | Content |
| --- | --- |
| `docs/SQL_DATA_HANDOFF.md` | Sources, tables, columns, constraints, identity and reference rules, import |
| `docs/DATA_GENERATION_GUIDE.md` | What new example data must contain; validation queries |
| `docs/GRAPH_DATA_HANDOFF.md` | Neo4j labels, relationships, constraints, indexes, graph API |
| `docs/PIPELINE_AND_LINKS_HANDOFF.md` | Build order, staleness, how everything links |
| `docs/*_LAYER_HANDOFF.md`, `docs/GRAPH_ALGORITHMS_HANDOFF.md` | One document per layer |
| `docs/AI_AGENT_HANDOFF.md` | The chat agent |
| `docs/SCALING_HANDOFF.md` | What to change before much larger data |
| `docs/SESSION_HANDOFF.md` | Current state and how to work in this repository |

## Project Structure

```text
backend/            Flask API (port 8000), graph layers, AI agent (backend/ai_agent/)
frontend/           React + Vite app (port 5173)
viewer/             SQL viewer and SQL-to-Neo4j import (port 5000)
scripts/            setup, run and helper scripts
docs/               documentation
draft/              early planning sketches
AGENTS.md           rules for coding agents
requirements.txt    Python dependencies
```

## Running

Always use the project virtual environment through the scripts (`AGENTS.md`). Local settings go in `.env`, which must
not be committed.

1. Start Neo4j: `.\scripts\run_neo4j.ps1` (stop it with `.\scripts\stop_neo4j.ps1`).
2. Start the backend and the frontend together:

   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_app.ps1
   ```

   or separately: `.\scripts\run_backend.ps1`, then in `frontend`: `npm.cmd run dev -- --host 127.0.0.1`.
3. Open `http://127.0.0.1:5173`.

The frontend needs the backend, and the backend needs Neo4j; without them the page opens but shows Neo4j as
disconnected. Install Python dependencies with `.\scripts\install_deps.ps1`; frontend dependencies with `npm install`
in `frontend`. `npm.cmd run build` builds the frontend for a server deployment.
