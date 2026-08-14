"""Covers app/lib/database.py -- the engine, the session factory, and the DI dependency.

Nothing here touches Postgres: the engine only dials out on the first query, and a
session does not connect when it is opened. That laziness is exactly what these tests
lean on, and it is the same property that keeps /health answering while the database
is down.
"""
import importlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

PLAIN_URL = 'postgresql://aryon:aryon@postgres:5432/aryondb'


@pytest.fixture
def database(monkeypatch):
    """A fresh app.lib.database, built from this test's environment by conftest isolation."""
    monkeypatch.setenv('DATABASE_URL', PLAIN_URL)
    return importlib.import_module('app.lib.database')


def test_plain_postgresql_url_is_driven_by_async_psycopg(database):
    """Relocated from the entrypoint test when the engine moved out of app/api/main.py."""
    assert database.engine.url.drivername == 'postgresql+psycopg'


def test_engine_is_built_from_the_configured_url(database):
    assert database.engine.url.database == 'aryondb'
    assert database.engine.url.host == 'postgres'


def test_sessions_survive_a_commit(database):
    """expire_on_commit=False, so converting rows to schemas after a commit cannot
    trigger a lazy refresh against a session that is about to close."""
    assert database.session_factory.kw['expire_on_commit'] is False


async def test_get_session_yields_an_async_session(database):
    sessions = [session async for session in database.get_session()]
    assert len(sessions) == 1
    assert isinstance(sessions[0], AsyncSession)


async def test_get_session_closes_the_session_afterwards(database, monkeypatch):
    """A leaked session holds its connection, so the pool drains under load."""
    generator = database.get_session()
    session = await anext(generator)

    closed = False
    original_close = session.close

    async def record_close():
        nonlocal closed
        closed = True
        await original_close()

    monkeypatch.setattr(session, 'close', record_close)

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert closed


def test_missing_database_url_fails_at_import(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        importlib.import_module('app.lib.database')
