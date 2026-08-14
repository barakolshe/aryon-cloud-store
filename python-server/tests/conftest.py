"""Shared test setup.

Several suites re-import the app package to exercise import-time behaviour: the engine is
built when `app.lib.database` is imported, and the settings when `app.config` is, so a test
that wants a different environment has to get a fresh import.

Clearing the leaf modules alone is not enough, and fails in a way that is easy to misread.
`from app.api.routes import health` in app/api/main.py reads the attribute the
`app.api.routes` package object still holds, so a surviving package hands back the
*previous* submodule while `import_module` builds a new one. The app then wires itself
from one copy while a test holds the other, which surfaces as a dependency override that
silently does not apply -- the endpoint reaches the real database instead of the stub.
Dropping the whole `app.*` tree keeps the two in step.

Modules already imported at collection time stay usable: a class removed from sys.modules
keeps working through the references its functions hold, so the bound names in a test
module still resolve to the objects that module imported.

The database fixtures below are the other half: a dedicated `aryondb_test`, migrated with
Alembic once per run and truncated before each test that asks for it.
"""
import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# A database of its own, never the one the running stack serves: these tests truncate
# between cases, and `python tests/run_tests.py` posts hierarchies into the development
# database at the same time. Sharing one would have them deleting each other's rows.
TEST_DATABASE = 'aryondb_test'

PYTHON_SERVER = Path(__file__).parents[1]


def forget_app_modules():
    for name in [name for name in sys.modules if name == 'app' or name.startswith('app.')]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def isolate_app_imports():
    """Give every test a clean slate, and leave one behind for the next."""
    forget_app_modules()
    yield
    forget_app_modules()


def pytest_asyncio_loop_factories(config, item):
    """Run the async tests on a selector loop when the developer is on Windows.

    psycopg refuses async mode on asyncio's ProactorEventLoop, which is Windows' default,
    so without this every database test fails at connect with an InterfaceError. The hook
    rather than the `event_loop_policy` fixture: pytest-asyncio 1.4 deprecated overriding
    that fixture and no longer builds its loops from the policy.

    Nothing about the server changes -- it runs on Linux in the container, where this
    returns the loop asyncio would have picked anyway.
    """
    if sys.platform == 'win32':
        return {'selector': asyncio.SelectorEventLoop}
    return {'default': asyncio.EventLoop}


def run_on_a_selector_loop(coroutine, **runner_arguments):
    """`asyncio.run`, but on a loop psycopg will talk to on Windows."""
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop, **runner_arguments) as runner:
        return runner.run(coroutine)


@pytest.fixture(scope='session')
def migrated_database_url():
    """Create the test database if it is missing, migrate it to head, and return its URL.

    Synchronous on purpose. alembic/env.py finishes with `asyncio.run`, which raises if it
    is called from inside a running event loop, so this cannot be an async fixture.

    Skips rather than fails when there is nothing to connect to, so the pure tests still
    run for someone without the stack up. The skip names the reason: a silent one here
    would look exactly like a passing suite.
    """
    forget_app_modules()
    try:
        # Imported through the one module allowed to read the environment, so the tests do
        # not become a second reader of DATABASE_URL.
        from app.config import settings
    except RuntimeError as error:
        pytest.skip(f'no database to test against -- {error}')

    development_url = make_url(settings.database_url)
    test_url = development_url.set(database=TEST_DATABASE)

    # AUTOCOMMIT because CREATE DATABASE cannot run inside a transaction block.
    admin_engine = create_engine(development_url, isolation_level='AUTOCOMMIT')
    try:
        with admin_engine.connect() as connection:
            already_there = connection.scalar(
                text('SELECT 1 FROM pg_database WHERE datname = :name'),
                {'name': TEST_DATABASE},
            )
            if already_there is None:
                # Not parameterisable -- an identifier, not a value -- but it is a constant
                # defined in this file rather than anything a test supplies.
                connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    except OperationalError as error:
        pytest.skip(f'Postgres is not reachable at {development_url.render_as_string()} -- {error}')
    finally:
        admin_engine.dispose()

    # Alembic rather than Base.metadata.create_all: the schema under test should be the one
    # production actually gets, migrations and all. env.py takes its URL from app.config and
    # nowhere else, so pointing it at the test database means setting the variable and
    # dropping the cached modules that already read it.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('DATABASE_URL', test_url.render_as_string(hide_password=False))
        if sys.platform == 'win32':
            # env.py finishes with asyncio.run(), which builds the ProactorEventLoop that
            # psycopg refuses -- the same Windows problem the loop-factory hook solves for
            # the tests, in the one place the hook does not reach.
            patch.setattr(asyncio, 'run', run_on_a_selector_loop)
        forget_app_modules()
        command.upgrade(Config(str(PYTHON_SERVER / 'alembic.ini')), 'head')
    forget_app_modules()

    return test_url


@pytest.fixture
async def database_engine(migrated_database_url):
    """An engine on the test database, with the tables emptied before the test runs.

    Function-scoped, engine and all. A session-scoped async fixture is the classic way into
    "attached to a different loop" failures, because pytest-asyncio gives each test its own
    loop while the pooled connections stay bound to the loop that opened them. At this suite
    size a fresh engine per test costs milliseconds.
    """
    engine = create_async_engine(migrated_database_url)
    async with engine.begin() as connection:
        # Both tables in one statement: node_closure references nodes, and Postgres refuses
        # to truncate a referenced table unless the referencing one goes with it.
        await connection.execute(text('TRUNCATE nodes, node_closure'))
    yield engine
    await engine.dispose()


def build_client(engine):
    """The application, with its session dependency pointed at the test database.

    Both modules are imported here rather than at the top of the file so they come from the
    same generation: `isolate_app_imports` above drops the `app.*` tree around every test,
    and overriding a `get_session` from an older import would leave the endpoint talking to
    the real engine.
    """
    main = importlib.import_module('app.api.main')
    database = importlib.import_module('app.lib.database')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_from_the_test_engine():
        async with session_factory() as session:
            yield session

    app = main.create_app()
    app.dependency_overrides[database.get_session] = session_from_the_test_engine
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.fixture
async def client(database_engine):
    """An HTTP client onto the real app, talking to the emptied test database."""
    async with build_client(database_engine) as client:
        yield client
