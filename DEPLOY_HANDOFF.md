# Deploy Handoff

Handoff for the Claude session that deploys AI2_VG to a server. Written 2026-09-30, checked against the code the same
day. Read `README.md`, `AGENTS.md` and `docs/SESSION_HANDOFF.md` first; this document covers only the deployment.

**Goal:** the app running on a Google Cloud VM for the presentation on **Friday 2026-10-02**. Anyone with the link can
look (graph, chat history, settings, test results); only the user can do anything that changes data or costs money.

## Working with this user

- Talk Swedish. Write code, config, comments, docs and commit messages in English.
- **Never install anything without asking twice**, on the local machine and on the server (Docker, packages, images,
  certbot, anything). This rule has no exceptions.
- **Confirm before any change**: state the plan, wait for an explicit "ja" / "kör".
- One step at a time; short answers; prove the cause before changing code.
- Commits: the user (Hassan Mehdi) is the only author, no `Co-Authored-By` line, English message written to a
  scratchpad file and committed with `git commit -F <file>`. Commit and push only when asked.
- Never print or commit `.env`, passwords or keys. The SSH private key stays on the user's machine.
- Anything that calls OpenAI costs money; the deployment itself needs no model calls.

## Decided plan

| Part | Decision |
| --- | --- |
| Cloud | Google Cloud free-trial credits, a **separate Google Cloud project** from the user's existing server |
| Machine | **One VM**, at least 8 GB RAM (for example `e2-standard-2`), firewall open for 22, 80, 443 only |
| Runtime | **Docker Compose** |
| Address | A **static IP**, no domain; HTTPS through **sslip.io** (for example `34-88-12-5.sslip.io`) with Let's Encrypt |
| Access for Claude | **SSH only**, as a `deploy` user with the user's SSH key. The user creates the VM, the static IP and the firewall in the console |
| Login | **Nginx basic auth** (the browser's own login prompt) for every request that is not a GET; GET is open |
| Neo4j | Official **Enterprise** image `neo4j:2026.08.1-enterprise` with `NEO4J_ACCEPT_LICENSE_AGREEMENT=eval` (evaluation license); keep the compose file able to switch to Community with one line |

## Services

| Service | Content | Port inside the network | Public |
| --- | --- | --- | --- |
| `nginx` | Serves the built frontend (`frontend/dist`), proxies `/api/` to the backend, HTTPS | 80, 443 | yes |
| `backend` | Flask app `backend/app.py` under Gunicorn; also starts the SQL viewer as a child process | 8000 (and 5000 for the viewer) | through Nginx |
| `neo4j` | Enterprise 2026.08.1, data in a volume | 7474, 7687 | no (SSH tunnel for the Neo4j Browser) |
| `postgres` | PostgreSQL 18, database `hm_data`, data in a volume | 5432 | no |

### Backend facts that shape the setup

- **One Gunicorn worker, several threads** (for example `gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 app:app` from
  `backend/`, timeout of at least 300 s). Chat conversations live in the process memory (`_threads` in
  `backend/ai_agent/graph.py`) and the SQL viewer process is tracked in a module variable (`backend/app.py`), so
  every request must reach the same process.
- The chat (`POST /api/ai/chat`) and the test run (`POST /api/ai/agent/evaluation`) stream **Server-Sent Events**; the
  backend already sends `X-Accel-Buffering: no`. In Nginx also set `proxy_buffering off` and a long
  `proxy_read_timeout` for `/api/`.
- `GET /api/graph` returns about 2.2 MB of JSON: turn on **gzip** for `application/json` in Nginx.
- The backend reads its settings from environment variables (`.env` through `python-dotenv`): `NEO4J_URI`,
  `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`,
  `OPENAI_API_KEY`. In Compose, `NEO4J_URI=neo4j://neo4j:7687` and `DB_HOST=postgres`. Put the server's `.env` on
  the VM only (mode 600), never in git or the image.
- The backend writes to `backend/ai_agent/settings.json`, `evaluation_last.json` and `evaluation_history.json`
  (settings and test runs): keep them in a volume or a writable bind mount so they survive a container restart.
- Same origin behind Nginx: the frontend calls `/api/...` relative, so no CORS setting is needed.
- `app.run(...)` at the bottom of `backend/app.py` is only for local development; Gunicorn imports `app` directly.
- On import, `backend/app.py` starts the agent warm-up (`backend/ai_agent/warmup.py`) in a background thread: it
  waits for Neo4j and runs the agent's queries once, so the first chat question is quick. It also runs under Gunicorn;
  look for `Agent warm-up done` in the backend log before a demo.

### The SQL viewer on the server

The `Open SQL Viewer` button calls `POST /api/viewer/start`, which starts `viewer/app.py` on `127.0.0.1:5000` and
returns `VIEWER_URL = "http://127.0.0.1:5000/"` (`backend/app.py`, line 50). On the server the viewer must be reached
through Nginx instead. Options to present to the user (their choice):

1. **Behind basic auth on its own Nginx server block** (for example port 8443, or `viewer.<ip>.sslip.io`), proxying to
   the backend container's port 5000; `VIEWER_URL` read from an environment variable with the local value as default.
2. **Not exposed on the server**: the data is already imported, and the viewer is reached through an SSH tunnel
   (`ssh -L 5000:localhost:5000 ...`) when needed.

Both keep the local setup unchanged.

## Nginx access rules

- `location /` : the static frontend (`try_files $uri /index.html`), open.
- `location /api/` : proxy to `backend:8000`. `GET` open; every other method behind basic auth, for example with
  `limit_except GET { auth_basic "AI2_VG"; auth_basic_user_file /etc/nginx/.htpasswd; }`. This covers the chat,
  every layer build, embeddings, settings, the test run and the viewer start.
- The `.htpasswd` file is created on the server with the user present; the password is chosen by the user.
- HTTPS: Let's Encrypt for the sslip.io name (certbot, or an image that includes it; installing it needs the user's
  double confirmation). Redirect 80 to 443.
- Optional frontend touch (only if the user asks): a small `Log in` button that sends one POST to trigger the browser
  prompt, and a hint that the page is read-only for visitors.

## Moving the data

**PostgreSQL.** Either load the example data (`data/kvitta_seed.sql`, gitignored, copied with `scp`) after running
the schema script, or `pg_dump` the local `hm_data` and restore it. Both give the same 263 rows.

**Neo4j (no rebuild, no OpenAI cost).** The local database is Neo4j Enterprise 2026.08.1 with store format
`block-block-1.1`, and the server runs the same Enterprise version, so the database moves as it is:

1. Stop the local Neo4j: `.\scripts\stop_neo4j.ps1`.
2. Dump it with `neo4j-admin database dump neo4j --to-path=<folder>` from the DBMS folder
   (`%USERPROFILE%\.Neo4jDesktop2\Data\dbmss\dbms-5bffa807-f4ad-44c1-b173-404843e05d7a`), with `JAVA_HOME` set as in
   `scripts/run_neo4j.ps1`.
3. Copy the `.dump` file to the VM with `scp`.
4. Load it into the server's volume with `neo4j-admin database load neo4j --from-path=<folder> --overwrite-destination`
   (run in a one-off container of the same image, with the Neo4j service stopped), then start Neo4j.
5. Check: 542 nodes, 2 851 relationships, the vector and fulltext indexes `ONLINE`, and the app shows every layer as
   up to date.

The code uses only built-in Neo4j features (vector and fulltext indexes, the Cypher `SEARCH` clause), no plugins.

If the user chooses Community instead: convert a copy of the database first with
`neo4j-admin database migrate --to-format=aligned`, then dump and load into `neo4j:2026.08.1-community`.

## Files to prepare locally (before the VM exists)

- `backend/Dockerfile`: Python image, `requirements.txt` plus `gunicorn`, the `backend/` and `viewer/` folders.
- `frontend` build: `npm.cmd run build` in `frontend` gives `frontend/dist`, served by Nginx (either built into the
  Nginx image or copied to the VM).
- `docker-compose.yml`: the four services, volumes for Neo4j, PostgreSQL and the agent's JSON files, one internal
  network, only Nginx publishing ports.
- `deploy/nginx.conf`: the rules above.
- `deploy/.env.example`: the keys without values.
- A short `deploy/README.md` (English): how to start, stop, update and back up on the VM.

Show each file to the user before creating it, and test the stack locally where possible.

## Order

1. Prepare the files above locally; the user reviews them.
2. The user creates the Google Cloud project, the VM, the static IP and the firewall, and adds the SSH key.
3. On the VM, with double confirmation for each install: Docker and Docker Compose.
4. Copy the files, the server `.env`, the Neo4j dump and the PostgreSQL data; load both databases.
5. Start the stack, get the HTTPS certificate, create `.htpasswd` with the user.
6. Check: the page loads over HTTPS, the graph shows 542 nodes, a GET works without login, a chat question asks for
   the login and then answers with sources.
7. Commit the deployment files when the user asks.

## Local reference

- Start locally: `scripts/run_neo4j.ps1`, `scripts/run_backend.ps1`, then `npm.cmd run dev -- --host 127.0.0.1` in
  `frontend` (`README.md`).
- Python always through `.venv` (`.\.venv\Scripts\python.exe`); dependencies through `scripts/install_deps.ps1`.
- PostgreSQL 18 runs locally as the Windows service `postgresql-x64-18`, read-only access with
  `C:\Program Files\PostgreSQL\18\bin\psql.exe`.
