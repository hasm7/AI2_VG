# Agent Instructions

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

## Language

Write code, comments, commit messages, README content, and project documentation in English.

Talk to the user in Swedish unless the user asks for another language.
