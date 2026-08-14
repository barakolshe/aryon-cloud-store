# CLAUDE.md

CloudStore stores a nested cloud hierarchy (management groups, subscriptions, resource groups) in
Postgres and serves it over HTTP. `POST /hierarchy` saves a hierarchy as an upsert — re-submitting a
changed hierarchy must handle added, removed, and moved nodes. `GET /hierarchy/{node_id}` returns
that node with all of its descendants nested underneath.

**This project implements the Python/Flask server** (`python-server/`, port 8081). The Go server is
left as the reference stub it shipped as — don't build features there.

## Rules

- **Ask before assuming**: When a task is ambiguous, requirements are unclear, or you're unsure about the intended approach, ask clarifying questions before proceeding. Don't guess — it's better to ask than to build the wrong thing.
- **Don't write code unless asked**: Default to investigation and explanation only. Do not write, edit, or commit code unless the user explicitly asks you to. Answer questions, diagnose issues, and propose solutions verbally first.
- **Write tests for code changes**: When you change or add code, add or update tests that cover the new behavior. This project has **no CI pipeline** — nothing runs your tests but you — so run the suite locally before opening a PR and paste the result in the PR description. Bring the stack up with `docker compose up --build -d`, then run `python tests/run_tests.py` from the repo root (it resolves `tests/objects` relative to the working directory). Never delete or weaken an existing test in `tests/` — the assignment forbids it.
- **Avoid default values**: Try to avoid giving variables and parameters default values unless a default is specifically needed. Prefer making callers pass values explicitly so intent is clear and missing values surface as errors instead of being silently filled in.
- **Environment variables**: Never read environment variables (`os.getenv`, `os.environ`, etc.) outside of a single central config module in `python-server/`. Today `DATABASE_URL` is read inline in `app.py` with a localhost fallback baked in — move it into that one config module, validate it there, and import it everywhere else. No other module should touch the environment.
- **Database schema**: The schema lives in `postgres/init.sql`. Postgres runs it **only when its data directory is empty**, so editing the file has no effect on an already-running database. To pick up a schema change, recreate the container and its data: `docker compose down -v && docker compose up --build -d`. That wipes all stored hierarchies, which is fine in development — just never assume an edit to `init.sql` applied itself. Write schema changes as edits to `init.sql`; do not create tables by hand against a running database, or the next person to start fresh gets a different schema than you have.
- **Workflow**: At the start of a task, pull the latest `dev`. Branch from `dev`, and when the work is done open a PR into `dev` — then **stop**. Do NOT merge it: the user reviews and merges every PR manually. Resolve any merge conflicts with `dev` before handing the PR over, and say plainly in the PR description what you ran and what passed. After the user merges, sync your local `dev` with the remote in the **main project directory** (where `dev` is checked out), not in a worktree — git won't update a branch checked out elsewhere.
- **Releasing to `main`**: Merging `dev` → `main` is a release. Always ask the user for explicit confirmation before opening a `dev` → `main` PR, run the full test suite locally first, and leave the merge itself to the user.

## Tools

- **Docker Compose** — the whole stack. `docker compose up --build -d` starts Postgres (5432), the Flask server (8081), and the Go stub (8080). `docker compose logs -f python-server` for server logs; `docker compose down -v` to reset the database.
- **Test suite** — `python tests/run_tests.py`, run from the repo root. It stores each hierarchy in `tests/objects/` and fetches it back, comparing the JSON. Note: the script's `port` variable ships as `8080` (the Go server) — it needs to point at `8081` for the Flask server.
- **psql** — inspect the database directly with `docker compose exec postgres psql -U aryon -d aryondb`.
- No MCP servers are configured for this project, and there is no hosted database — Postgres runs locally in Docker.
