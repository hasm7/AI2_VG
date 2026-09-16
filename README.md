# AI2_VG

Detta projekt modellerar simulerade datakällor från ett software engineering-team.

Syftet är att bevara källmaterial i PostgreSQL inför senare import till en grafdatabas. SQL-lagret ska hålla originaldata, stabila ID:n, tider, versioner och källhänvisningar. Grafdatabasen är sedan tänkt att användas för att hitta samband mellan personer, behov, diskussioner, beslut, ärenden, dokumentation och implementation.

## Datakällor

Projektet utgår från sex typer av källor:

- Mail
- Slack/projektchatt
- Teams/mötestranskript
- Ärenden/tickets
- Krav och teknisk dokumentation
- PR:er, kodgranskningar och kodändringar

## SQL-tabeller

Databasschemat består av nio tabeller:

- `mail_messages`
- `slack_messages`
- `teams_meetings`
- `teams_transcript_segments`
- `issue_versions`
- `issue_comments`
- `document_versions`
- `pr_versions`
- `pr_reviews`

## JSONB-fält

Vissa fält sparas som `JSONB` eftersom de naturligt är listor eller strukturer:

- mailmottagare
- mötesdeltagare
- kodändringar i PR:er

Det gör att källmaterialet kan bevaras utan onödig uppdelning i fler SQL-tabeller.

## Projektstruktur

```text
docs/
  BESLUTAD_PROJEKTINRIKTNING.md
draft/
  plan_initial.PNG
  plan_initial2.PNG
scripts/
  setup_postgres_schema.py
README.md
requirements.txt
```

## Databas

Projektet använder PostgreSQL.

Schemat finns i:

```text
scripts/setup_postgres_schema.py
```

Lokala databasinställningar ska ligga i `.env`. Den filen ska inte commitas till repo.
