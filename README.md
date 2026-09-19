# AI2_VG

This project models simulated data sources from a software engineering team.

The purpose is to preserve source material in PostgreSQL before later importing it into a graph database. The SQL layer stores original data, stable IDs, timestamps, versions, and source references. The graph database is intended to find connections between people, needs, discussions, decisions, issues, documentation, and implementation.

## Data Sources

The project uses six source types:

- Mail
- Slack/project chat
- Teams/meeting transcripts
- Issues/tickets
- Requirements and technical documentation
- Pull requests, code reviews, and code changes

## SQL Tables

The database schema contains nine tables:

- `mail_messages`
- `slack_messages`
- `teams_meetings`
- `teams_transcript_segments`
- `issue_versions`
- `issue_comments`
- `document_versions`
- `pr_versions`
- `pr_reviews`

## JSONB Fields

Some fields are stored as `JSONB` because they naturally contain lists or structured values:

- mail recipients
- meeting participants
- code changes in pull requests

This keeps the source material structured without splitting it into unnecessary extra SQL tables.

## Project Structure

```text
docs/
  BESLUTAD_PROJEKTINRIKTNING.md
draft/
  plan_initial.PNG
  plan_initial2.PNG
scripts/
  install_deps.ps1
  run_app.ps1
  run_backend.ps1
  run_viewer.ps1
  setup_postgres_schema.py
backend/
  app.py
frontend/
  src/
viewer/
  app.py
AGENTS.md
README.md
requirements.txt
```

## Database

The project uses PostgreSQL.

The schema is defined in:

```text
scripts/setup_postgres_schema.py
```

Local database settings should be placed in `.env`. That file must not be committed to the repository.

## Running the Graph App

The React frontend depends on the backend API, and the backend API depends on Neo4j.
Starting only the React server opens the page, but the graph will show Neo4j as disconnected unless the backend is already running.

Start the full graph app with:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_app.ps1
```

This starts the backend on `http://127.0.0.1:8000`, waits until it responds, and then starts the React frontend on `http://127.0.0.1:5173`.

Use the frontend-only command only when the backend is already running:

```powershell
cd frontend
npm.cmd run dev -- --host 127.0.0.1
```

## Frontend

The frontend lives in `frontend/`. To set it up:

```text
npm install
npm.cmd run dev -- --host 127.0.0.1  # frontend only
npm.cmd run build                    # production build
```
