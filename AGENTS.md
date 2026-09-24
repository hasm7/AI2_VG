# Agent Instructions

## Project Context

This project models simulated source material from a software engineering team.
PostgreSQL is used as the source-preserving SQL layer before later import into a
graph database / GraphRAG memory system.

There are six logical data sources, even though the SQL schema has ten tables:

- Mail: `mail_messages`
- Slack / project chat: `slack_messages`
- Teams / meeting transcripts: `teams_meetings`, `teams_transcript_segments`
- Issues / tickets: `issues`, `issue_versions`, `issue_comments`
- Requirements and technical documentation: `document_versions`
- Pull requests, code reviews, and code changes: `pr_versions`, `pr_reviews`

The SQL viewer is source-oriented, not only table-oriented. Some viewer sections
combine multiple tables to show one logical data source, such as Teams, Issues,
and PRs.

## Python Environment

Always use the project virtual environment.

Use the project scripts. Do not run Python directly.

Install dependencies with:

```powershell
.\scripts\install_deps.ps1
```

Run the data viewer with:

```powershell
.\scripts\run_viewer.ps1
```

These scripts use:

```powershell
.\.venv\Scripts\python.exe
```

Do not use bare `python`, `pip`, or global/user Python for this project.

Install dependencies only with:

```powershell
.\scripts\install_deps.ps1
```

Before running Python commands, check that the command uses `.venv`.

## Database

Do not modify the database unless the user explicitly asks for it.

Read-only database checks are allowed when requested.

For local PostgreSQL access, prefer the project `.env` settings. Do not commit `.env`.

For local PostgreSQL inspection, use the local `psql` client directly and run
read-only queries. Use connection values from `.env` at runtime only; never
commit credentials, `.env` contents, or sensitive query output.

On this machine, use:

```powershell
C:\Program Files\PostgreSQL\18\bin\psql.exe
```

If the PostgreSQL service must be started or stopped, ask for elevated
permission first.


## Language

Write code, comments, commit messages, README content, and project documentation in English.

Talk to the user in Swedish unless the user asks for another language.
