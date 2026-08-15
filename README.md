# CloudStore

Stores a nested cloud hierarchy — management groups, subscriptions, resource groups — in Postgres
and serves it over HTTP.

| Method | Path                    | Behaviour                                                                       |
| ------ | ----------------------- | ------------------------------------------------------------------------------- |
| `POST` | `/hierarchy`            | Upsert a hierarchy. Handles added, removed and moved nodes in one request.        |
| `GET`  | `/hierarchy/{node_id}`  | Return that node with every descendant nested underneath it. `404` if unknown.    |
| `GET`  | `/health`               | Liveness. Touches no database, so it answers `200` with Postgres stopped.         |

The original assignment brief lives in [`exercise.md`](exercise.md); design decisions this
implementation deliberately left on the table are in [`notes.md`](notes.md).

The implementation is the **Python server** (`python-server/`, port 8081) — FastAPI, SQLAlchemy 2.0,
Alembic, psycopg 3, async from the socket to the route. The Go server on port 8080 is the reference
stub the exercise shipped with and is untouched.

## Running it

The server runs in Docker, so Docker Compose is the only prerequisite for bringing it up. Running the
tests from the host additionally wants [`uv`](https://docs.astral.sh/uv/getting-started/installation/),
which manages the Python version and the locked dependency set for you — there is no virtualenv to
create and nothing to `pip install`.

```bash
docker compose up --build -d
```

That builds three containers — Postgres (5432), the Python API (8081), and the Go stub (8080). The
API container runs `alembic upgrade head` before starting uvicorn, so a fresh clone gets the full
schema without a separate step, and the server can never come up against a schema it does not match:
a failed migration stops the container.

```bash
curl localhost:8081/health
curl -X POST localhost:8081/hierarchy -H 'content-type: application/json' -d @tests/objects/1.json
curl localhost:8081/hierarchy/1
```

`docker compose logs -f python-server` for server logs, `docker compose down -v` to stop everything
and discard the stored data.

### Tests

Two suites, both run against the stack started above — the fixture suite needs the API on 8081, the
pytest suite needs only Postgres on 5432.

**The assignment's fixture suite** — posts each hierarchy in `tests/objects/` and fetches it back,
comparing the JSON. Run from the repo root, which is where it resolves `tests/objects` from:

```bash
uv run --no-project --with-requirements tests/requirements.txt python tests/run_tests.py
```

It prints a `Passed n/6` tally and exits non-zero if any fixture fails. Without `uv`, install
`tests/requirements.txt` into a Python 3 environment and run `python tests/run_tests.py`.

<sub>On Windows, prefix it with `PYTHONIOENCODING=utf-8` — the script prints ✅/❌ and a cp1252 console
cannot encode them. The script is the assignment's and must not be edited, so the encoding is set from
outside.</sub>

**The pytest suite** — unit and integration tests, run from `python-server/` with `uv`, which
resolves the locked dependency set itself:

```bash
cd python-server
DATABASE_URL=postgresql://aryon:aryon@localhost:5432/aryondb uv run pytest
```

<sub>PowerShell: `$env:DATABASE_URL='postgresql://aryon:aryon@localhost:5432/aryondb'; uv run pytest`</sub>

The integration tests create and migrate a database of their own, `aryondb_test`, so they never
truncate rows out from under the fixture suite. Without a reachable Postgres they skip with a reason
rather than fail, and the tests that need no database still run.

### Migrations

Alembic owns the schema. `postgres/init.sql` is intentionally empty: Postgres runs it only when the
data directory is empty, which makes it a poor home for a schema that has to change. Migrations run
on every container start and are versioned alongside the code that expects them.

Run them in the API container, which already has the environment and the locked dependencies:

```bash
docker compose exec python-server uv run alembic upgrade head                      # apply what is pending
docker compose exec python-server uv run alembic current                           # what the database is at
docker compose exec python-server uv run alembic downgrade base                    # unwind to an empty schema
docker compose exec python-server uv run alembic revision --autogenerate -m "..."  # diff app/lib/models/ into a revision
```

The same commands run from `python-server/` on the host with `DATABASE_URL` set, on Linux and macOS.
They do not on Windows: `alembic/env.py` ends in `asyncio.run`, which builds the `ProactorEventLoop`
that psycopg refuses to talk to. The container is Linux, so it is the portable answer. (`conftest.py`
patches around this for the test suite, which is why `uv run pytest` works on the host either way.)

Autogenerate diffs against `Base.metadata`, so a new model is invisible to it until it is imported in
`alembic/env.py`. Review what it writes: it handles Postgres enums poorly, and the initial revision is
hand-written for exactly that reason. Note that `python-server/` is copied into the image rather than
mounted, so a revision generated in the container stays there — `docker compose cp
python-server:/app/alembic/versions/<file> python-server/alembic/versions/` brings it back out.

### Configuration

One variable, `DATABASE_URL`, read in one place — `python-server/app/config.py`. It has no default:
an unset or malformed URL is a startup failure that names the variable, not a 500 on whichever
request first needs the database. A plain `postgresql://` URL is rewritten to `postgresql+psycopg://`
there, so nothing outside that module has to know about dialect strings.

## Architecture

`python-server/app/` splits in two, and the split is a one-way dependency:

```
app/
├── config.py                     the only module that reads the environment
├── api/                          the HTTP layer — the only place FastAPI is imported
│   ├── main.py                   app assembly: routers and error handlers, nothing else
│   ├── errors.py                 every exception → status code, mapped once
│   ├── routes/                   paths, status codes, dependency wiring
│   └── schemas/                  HTTP-only response shapes (Pydantic)
└── lib/                          the reusable layer — imports nothing from api/
    ├── hierarchy.py              HierarchyService: the use cases
    ├── repositories/             base.py declares the interface, postgres_hierarchy.py implements it
    ├── models/                   SQLAlchemy tables
    ├── types/                    domain types shared by both layers
    └── database.py               engine, session factory, per-request session
```

A request goes **route → service → repository**:

```mermaid
flowchart LR
    R["routes/hierarchy.py<br/><i>HTTP only</i>"] --> S["HierarchyService<br/><i>use cases, transaction boundary</i>"]
    S --> I["HierarchyRepository<br/><i>abstract interface</i>"]
    I -.implemented by.-> P["PostgresHierarchyRepository<br/><i>all the SQL</i>"]
```

Routes speak HTTP and nothing else — paths, status codes, dependency injection. `HierarchyService`
owns orchestration and the unit of work. `PostgresHierarchyRepository` owns every statement.

**`app/lib/` never imports from `app/api/`.** That is the whole point of the split: it is what lets a
second front end — a CLI, a worker, another API — call `HierarchyService.store_hierarchy` without
dragging FastAPI along. The service holds a `HierarchyRepository`, an abstract interface that speaks
ids and plain `NamedTuple`s from `app/lib/types/rows.py` and never an `AsyncSession` or a SQLAlchemy
`Row`. `commit()` is on that interface rather than something a caller reaches around it for, because
one repository instance is one unit of work.

Choosing the implementation is the one decision `app/lib/` deliberately does not make. `build_service`
in `app/api/routes/hierarchy.py` is the composition root and the only place that names
`PostgresHierarchyRepository`.

### Async end to end

`create_async_engine` + `async_sessionmaker`, every endpoint a coroutine, every database call awaited,
and psycopg 3's async dialect underneath — `psycopg2` has no async support at all. Alembic's `env.py`
is async too, driving migrations through `async_engine_from_config` and handing the connection to
`run_sync`, since migration operations themselves are ordinary synchronous code.

One blocking call inside a coroutine stalls every in-flight request, so there are none. If blocking
work ever becomes unavoidable it goes through `run_in_threadpool`, explicitly.

Tree assembly and payload flattening are iterative rather than recursive for the same reason: the
recursive version puts the depth of a customer's hierarchy against Python's recursion limit, and runs
that whole call chain on the event loop without an await in it.

## Data model

Two tables. `nodes` holds the shape in a `parent_id` column; `node_closure` holds one row for every
ancestor-descendant pair, including a depth-0 self-row per node.

```
nodes(id BIGINT PK, type node_type, parent_id BIGINT NULL → nodes.id ON DELETE RESTRICT)
node_closure(ancestor_id, descendant_id, depth, PK(ancestor_id, descendant_id))
```

The closure table is a derived index over `parent_id`. It records no fact that walking `parent_id`
would not produce — it exists because walking `parent_id` means a recursive CTE.

### The trade

**Reads are one indexed query.** `GET /hierarchy/{node_id}` is a single range scan on the closure's
primary key joined to `nodes`, ordered by `(depth, id)`, and the service nests the flat result in one
forward pass. No recursive CTE, no second query for the root — its depth-0 self-row is in the same
result set — and no post-hoc sort, since ordering by depth guarantees a parent is materialised before
its children and the id tiebreak gives the ascending sibling order the fixtures are compared against.

The recursive CTE it replaces is not slow because of the recursion. It is slow because Postgres always
materialises one and sizes it with a hardcoded guess of ten iterations. Measured against a synthetic
100k-node tree, the planner estimated 201 rows for every subtree asked of it — whether the answer was
2 rows or 34,464 — and touched 86,259 buffers to return the large one. The closure read estimated
35,458 against an actual 34,464 and touched 371.

**Writes pay for it.** A `POST` rebuilds the closure rows of every node it names: one row per node per
ancestor, so O(nodes × depth) rows on a move. Moving a node between siblings changes one ancestor per
descendant; moving a subtree across trees changes all of them.

**Reads were chosen as the hot path.** A cloud hierarchy is read constantly and reorganised rarely,
and the read is the one on a user-facing request. Cheap reads are worth an expensive write here.

### Integrity

`nodes.parent_id` is a single column, so a node structurally cannot acquire two parents. The
derived copy needs its own guarantee, and gets one:

```sql
CREATE UNIQUE INDEX uq_node_single_parent ON node_closure (descendant_id) WHERE depth = 1;
```

At most one depth-1 row per node ⇒ at most one parent ⇒ the stored graph can only ever be a forest.
Postgres enforces it, so no future caller can write around it, and a write path bug that emitted two
depth-1 rows for one node is a refused request rather than a corrupted hierarchy.

What no constraint can check is the two representations disagreeing about *which* node the parent is —
both would be singular and self-consistent. The write path setting them together, in one transaction,
is what keeps that from happening, and the tests assert the agreement directly rather than trusting it.

The rest of the schema carries the same intent. `parent_id` is `ON DELETE RESTRICT`, not `CASCADE`:
cascading there is recursive, so one mistyped `DELETE` would take an arbitrary amount of the hierarchy
with it and report `DELETE 1` while doing it. Closure rows do cascade, in both directions — they are
bookkeeping about a node and should follow it out. `depth >= 0` rather than `> 0` because of the
self-rows. `node_type` is a native Postgres enum, so the database rejects a fourth kind of node rather
than storing it.

### The write path

`POST /hierarchy` is an upsert, and the payload is authoritative for the entire subtree under every
node it mentions: anything currently stored beneath a payload node that the payload does not list is
removed. That one rule is what makes additions, removals and moves — including a node migrating in
from a different tree — fall out of a single request.

Two orderings inside it are load-bearing, and both are explained where they live in
`app/lib/hierarchy.py`:

- **Upsert before delete.** A node the request moves *out* of a subtree that is going away still
  points at its old parent until the upsert rewrites it, and `RESTRICT` refuses the delete until it
  does. Re-parenting first clears the hazard. The subsequent delete can still name a whole subtree in
  one statement, because a non-deferrable foreign key is checked when the statement finishes rather
  than row by row — which is also why the upsert can be one `INSERT` whose rows reference parents
  appearing later in the same `VALUES` list, with no topological sort.
- **The delete set unions over every payload id**, not just the root's descendants. A payload may name
  a node that currently lives somewhere else entirely; scoping the delete to the root would leave that
  node's unlisted children orphaned.

Writes take a `pg_advisory_xact_lock`, which serialises all hierarchy writes — including to disjoint
trees. That is a deliberate simplicity-for-throughput trade: a write reads the stored shape, decides
what to remove from it, and rewrites both representations, so two writes touching any node in common
can interleave into a state that matches neither payload. Should throughput ever matter more, the
upgrade is `SERIALIZABLE` plus a retry wrapper, not a finer-grained lock — row-level `FOR UPDATE` on
the payload ids cannot work, because the set a write affects includes rows that do not exist yet.

## Errors

One body shape, `{"detail": "..."}`, for every refusal, built in `app/api/errors.py` — Pydantic's
stock 422 shape is normalised to match, so a client parses errors the same way whatever produced them.

| Status | Cause                                                                      |
| ------ | -------------------------------------------------------------------------- |
| `404`  | No node with that id.                                                        |
| `409`  | A payload that cannot be stored: a repeated id, or one that would form a cycle. Also any `IntegrityError`, so a constraint refusal is a refused request rather than a stack trace. |
| `422`  | The request did not match the schema — unknown `type`, extra key, id outside `BIGINT`. |

Status codes are decided once, on the app, rather than per route: no endpoint carries a `try/except`
for a domain error. Error bodies never carry SQL text or connection strings; the specifics go to the
log, where the operator can see them and the caller cannot.
