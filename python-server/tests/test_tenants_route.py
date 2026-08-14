"""Covers GET /tenants end to end through the layers, with the repository stubbed out.

The body asserted here is the one `dev` served before the routes/controllers/repositories
split, so this is the regression guard on "the response did not change".
"""
import importlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

DATABASE_URL = 'postgresql://aryon:aryon@postgres:5432/aryondb'

# The rows postgres/init.sql seeds, in the order an unordered SELECT returns them.
SEEDED_TENANTS = [
    ('6901e1d7-1b6b-4001-8f13-f4b7e29ef1f1', 'microsoft'),
    ('e6a07004-6bc5-4c1b-9eb5-e48d30c159f2', 'amazon'),
    ('9c3f1845-c7ee-4b7f-806d-0a0e3e28fbeb', 'google'),
]


class StubTenantRepository:
    """Stands in for the real repository, so the route can be exercised with no database.

    Substituting at this level rather than mocking the session keeps the controller and
    the route under test -- only the SQL is replaced.
    """

    def __init__(self, rows):
        self._rows = rows

    async def list_all(self):
        return self._rows


class Row:
    """A stored row as the ORM hands it over: plain attributes, no behaviour."""

    def __init__(self, tenant_id, tenant_name):
        self.tenant_id = tenant_id
        self.tenant_name = tenant_name


@pytest.fixture
def modules(monkeypatch):
    """Import the entrypoint, then the route module it wired itself from.

    Importing app.main first is what makes these the same module object -- conftest
    cleared the whole app package, so main's `from app.routes import tenants` builds it
    and import_module then finds that one cached. Override keys have to be the very
    function the app holds, or the override silently does not apply.
    """
    monkeypatch.setenv('DATABASE_URL', DATABASE_URL)
    main = importlib.import_module('app.main')
    tenants = importlib.import_module('app.routes.tenants')
    assert main.tenants is tenants
    return main, tenants


def build_app_serving(modules, rows):
    """Build an app whose tenant controller reads from `rows` instead of Postgres."""
    main, tenants = modules
    controller_module = importlib.import_module('app.controllers.tenant')

    app = main.create_app()
    app.dependency_overrides[tenants.get_tenant_controller] = (
        lambda: controller_module.TenantController(StubTenantRepository(rows))
    )
    return app


async def get_tenants(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        return await client.get('/tenants')


async def test_tenants_returns_the_stored_rows_unchanged(modules):
    rows = [Row(uuid.UUID(tenant_id), name) for tenant_id, name in SEEDED_TENANTS]
    response = await get_tenants(build_app_serving(modules, rows))

    assert response.status_code == 200
    assert response.json() == [
        {'tenant_id': tenant_id, 'tenant_name': name} for tenant_id, name in SEEDED_TENANTS
    ]


async def test_tenants_preserves_repository_order(modules):
    """The repository issues no ORDER BY, so the route must not reorder either."""
    rows = [Row(uuid.UUID(tenant_id), name) for tenant_id, name in SEEDED_TENANTS]
    response = await get_tenants(build_app_serving(modules, rows))

    assert [row['tenant_name'] for row in response.json()] == ['microsoft', 'amazon', 'google']


async def test_tenant_ids_serialise_as_plain_uuid_strings(modules):
    """What the raw-SQL version emitted, and what any existing client already parses."""
    rows = [Row(uuid.UUID(SEEDED_TENANTS[0][0]), 'microsoft')]
    body = (await get_tenants(build_app_serving(modules, rows))).json()

    assert body[0]['tenant_id'] == SEEDED_TENANTS[0][0]
    assert type(body[0]['tenant_id']) is str


async def test_no_tenants_returns_an_empty_list(modules):
    response = await get_tenants(build_app_serving(modules, []))
    assert response.status_code == 200
    assert response.json() == []


async def test_response_carries_no_extra_keys(modules):
    rows = [Row(uuid.UUID(SEEDED_TENANTS[0][0]), 'microsoft')]
    body = (await get_tenants(build_app_serving(modules, rows))).json()
    assert set(body[0]) == {'tenant_id', 'tenant_name'}
