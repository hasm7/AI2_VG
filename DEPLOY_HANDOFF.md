# Deploy Handoff

How the app runs on its server, and how to update it. Checked against the server on 2026-10-08. Read `README.md`,
`AGENTS.md` and `docs/SESSION_HANDOFF.md` first; the commands are in `deploy/README.md`.

## Working with this user

- Talk Swedish. Write code, config, comments, docs and commit messages in English.
- **Never install anything without asking twice**, on the local machine and on the server (Docker, packages, images,
  anything). This rule has no exceptions. A rebuild whose package steps come from Docker's cache installs nothing new.
- **Confirm before any change**: state the plan and its parts, wait for an explicit "ja" / "kör".
- One step at a time; short answers; prove the cause before changing code.
- Commits: the user (Hassan Mehdi) is the only author, no `Co-Authored-By` line, English message written to a
  scratchpad file and committed with `git commit -F <file>`. Commit and push only when asked.
- Never print or commit `.env`, passwords or keys. The SSH private key stays on the user's machine.
- Anything that calls OpenAI costs money; deploying needs no model calls.

## The server

| Part | Value |
| --- | --- |
| Machine | Google Cloud VM, e2-standard-4 (4 vCPU, 16 GB), Ubuntu 24.04, its own Google Cloud project |
| Address | A static IP, plain HTTP on port 80; the SQL viewer at `viewer.<ip with dashes>.sslip.io` |
| Access for Claude | SSH only, as the deploy user with the user's key (`ssh -i <key> <user>@<ip>`) |
| Project folder | `~/ai2_vg` |
| What runs | `~/ai2_vg/DEPLOYED_VERSION`: branch, commit, date, the earlier version and the backup |

## Services (`docker-compose.yml`)

| Service | Content | Reached |
| --- | --- | --- |
| `nginx` | The built frontend; proxies `/api/` to the backend; the login | public, port 80 |
| `backend` | Flask under Gunicorn (one worker, eight threads); starts the SQL viewer as a child process on request | through Nginx |
| `graphrag` | Microsoft GraphRAG service (Python 3.13, `graphrag_service/Dockerfile`) | only by the backend, `http://graphrag:8100` |
| `neo4j` | `neo4j:2026.08.1-enterprise` with the evaluation license, data in a volume | SSH tunnel for the Neo4j Browser |
| `postgres` | PostgreSQL 18, database `hm_data`, data in a volume | inside the network |

**Login.** Looking is open; every request to `/api/` that is not a GET needs the login (Nginx basic auth, the
browser's own prompt): the chat, layer builds, embeddings, settings, test runs, the SQL viewer start and every
Microsoft GraphRAG export, build, question and evaluation run. The SQL viewer needs the login even for looking.

**Files that live only on the server:** `.env` (mode 600), `state/` (the agent's `settings.json`, `evaluation_last.json`,
`evaluation_history.json`, `graphrag_evaluation_last.json`, and `.htpasswd`), `dumps/`, and `graphrag_project/input`
and `output` (copied from the local machine).

## Updating the server

1. **Code** from a commit, with LF line endings like the rest of the server's files:

   ```powershell
   git -c core.autocrlf=false archive --format=tar -o code.tar <commit>
   scp -i <key> code.tar <user>@<ip>:/home/<user>/code.tar
   ```

   On the server: `tar -xf /home/<user>/code.tar -C /home/<user>/ai2_vg`, then remove the archive. `.env`, `state/`,
   `dumps/` and the GraphRAG data are not in git and stay as they are. Compare checksums (`sha256sum`) before
   unpacking.
2. **GraphRAG data**, when the index was rebuilt locally: `graphrag_project/input` and `output` with `tar` (keeps the
   file times), see `deploy/README.md`. The index is never built on the server.
3. `docker compose up -d --build`: only the services whose files changed are rebuilt and restarted; Neo4j and
   PostgreSQL keep running.
4. Update `DEPLOYED_VERSION`.
5. Check from outside: the start page, `/api/neo4j/status`, the agent and graph, every GraphRAG view
   (`/api/graphrag/status`, `config`, `index` up to date, `entities`, `communities`, `graph`, `evaluation`), and that
   every POST without login answers 401.

## History

- 2026-09-30: first deploy (`main` @ `a6951f4`): four services, HTTP, the SQL viewer behind the login.
- 2026-10-03: the IP changed (same VM); only `SERVER_NAME` in `.env` and `docker compose up -d` were needed.
- 2026-10-08: Microsoft GraphRAG added from the branch `microsoft-graphrag` (the `graphrag` service, the index copied
  from the local machine). The code before it is kept in `~/ai2_vg_code_before_graphrag_2026-10-08.tar.gz`.

## Moving the databases (first deploy)

**PostgreSQL:** load `data/kvitta_seed.sql` after the schema script, or restore a `pg_dump` of the local `hm_data`.

**Neo4j (no rebuild, no OpenAI cost):** the local database is Enterprise 2026.08.1 (store format `block-block-1.1`),
the same as the server's, so it moves as it is: stop the local Neo4j, `neo4j-admin database dump neo4j
--to-path=<folder>` from the DBMS folder with `JAVA_HOME` as in `scripts/run_neo4j.ps1`, copy the dump, and load it on
the server with the Neo4j service stopped (`deploy/README.md`). Check 542 nodes, 2 851 relationships and the vector and
fulltext indexes `ONLINE`. For Community, convert a copy first with `neo4j-admin database migrate --to-format=aligned`.
