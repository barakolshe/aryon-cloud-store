"""Covers GET /health -- the liveness check that must not touch the database."""
import importlib

from httpx import ASGITransport, AsyncClient

REACHABLE_URL = 'postgresql://aryon:aryon@postgres:5432/aryondb'
# A host that does not resolve: any attempt to query would raise rather than hang.
UNREACHABLE_URL = 'postgresql://aryon:aryon@no-such-host.invalid:5432/aryondb'


def build_app(monkeypatch, database_url):
    monkeypatch.setenv('DATABASE_URL', database_url)
    return importlib.import_module('app.api.main').create_app()


async def get_health(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        return await client.get('/health')


async def test_health_returns_ok(monkeypatch):
    response = await get_health(build_app(monkeypatch, REACHABLE_URL))
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


async def test_health_answers_with_the_database_unreachable(monkeypatch):
    """The acceptance criterion, asserted rather than eyeballed: /health depends on no
    session, so pointing the engine at a host that cannot be resolved changes nothing."""
    response = await get_health(build_app(monkeypatch, UNREACHABLE_URL))
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
