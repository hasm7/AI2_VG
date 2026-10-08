# Deploying AI2_VG

The app runs on one VM with Docker Compose: `neo4j`, `postgres`, `backend` (Gunicorn), `graphrag` (Microsoft GraphRAG,
below) and `nginx` (the built frontend and the login for everything that is not a GET). Only Nginx is public, on port 80 (plain HTTP): the app at
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

## Microsoft GraphRAG

The `graphrag` service (`graphrag_service/Dockerfile`, Python 3.13 with `requirements-graphrag.txt`, about 1 GB) answers
the Microsoft GraphRAG tab and the chat's MS GraphRAG mode. It has no published port: only the backend reaches it, at
`http://graphrag:8100`. The rest of the app works without it.

Its project folder `graphrag_project/` is mounted into both the `graphrag` and the `backend` container. `settings.yaml`
and the prompts come with the code; the input and the index are not in git and are copied from the local machine, with
`tar` so the files keep their times (the Index tab shows the time of the build):

```sh
# locally, in the project folder
tar -czf graphrag_data.tar.gz graphrag_project/input graphrag_project/output
scp -i <key> graphrag_data.tar.gz <user>@<ip>:~/ai2_vg/

# on the server, in ~/ai2_vg
tar -xzf graphrag_data.tar.gz && rm graphrag_data.tar.gz
touch state/graphrag_evaluation_last.json   # or copy the local backend/graphrag_evaluation_last.json there
docker compose up -d --build
docker compose logs -f graphrag             # wait for "GraphRAG warm-up done"
```

The index is built locally (the Index tab, with the cache), not on the server. Every GraphRAG request that is not a GET
(export, build, questions, evaluation runs) needs the login, like the rest of the app.

## Everyday use

| Task | Command |
| --- | --- |
| Status | `docker compose ps` |
| Logs | `docker compose logs -f backend` (or `nginx`, `neo4j`, `graphrag`) |
| Stop / start | `docker compose stop` / `docker compose start` |
| Update the code | copy the new code with LF line endings (`git -c core.autocrlf=false archive`, see `DEPLOY_HANDOFF.md`), then `docker compose up -d --build`; note the commit in `DEPLOYED_VERSION` |
| Neo4j Browser | `ssh -L 7474:localhost:7474 -L 7687:localhost:7687 <user>@<ip>`, then http://localhost:7474 |
| Back up Neo4j | `docker compose stop neo4j`, then `docker compose run --rm -v "$PWD/dumps:/dumps" neo4j neo4j-admin database dump neo4j --to-path=/dumps` |
