# Deploying AI2_VG

The app runs on one VM with Docker Compose: `neo4j`, `postgres`, `backend` (Gunicorn) and `nginx` (the built frontend
and the login for everything that is not a GET). Only Nginx is public, on port 80 (plain HTTP): the app at
`http://<ip>/`, the SQL viewer at `http://viewer.<SERVER_NAME>/`.

Commands run in the project folder on the server.

## First start

1. Copy the project to the server and create the server's `.env` from `deploy/.env.example` (`chmod 600 .env`).
2. Create the files the containers mount:

   ```sh
   mkdir -p state
   cp backend/ai_agent/settings.json state/settings.json
   touch state/evaluation_last.json state/evaluation_history.json   # or copy the local ones
   printf '<login>:%s\n' "$(openssl passwd -apr1)" > state/.htpasswd  # asks for the password
   ```

3. Load the databases (see below), then start everything:

   ```sh
   docker compose up -d --build
   docker compose logs -f backend   # wait for "Agent warm-up done"
   ```

## Loading the data

**Neo4j** (the local database, dumped with `neo4j-admin database dump neo4j --to-path=<folder>`), with the `neo4j`
service stopped:

```sh
docker compose run --rm -v "$PWD/dumps:/dumps" neo4j \
  neo4j-admin database load neo4j --from-path=/dumps --overwrite-destination
```

**PostgreSQL** (a `pg_dump --format=custom` of the local `hm_data`):

```sh
docker compose up -d postgres
docker compose exec -T postgres pg_restore -U postgres -d hm_data --no-owner < dumps/hm_data.dump
```

## Everyday use

| Task | Command |
| --- | --- |
| Status | `docker compose ps` |
| Logs | `docker compose logs -f backend` (or `nginx`, `neo4j`) |
| Stop / start | `docker compose stop` / `docker compose start` |
| Update the code | copy the new code, then `docker compose up -d --build` |
| Neo4j Browser | `ssh -L 7474:localhost:7474 -L 7687:localhost:7687 <user>@<ip>`, then http://localhost:7474 |
| Back up Neo4j | `docker compose stop neo4j`, then `docker compose run --rm -v "$PWD/dumps:/dumps" neo4j neo4j-admin database dump neo4j --to-path=/dumps` |
