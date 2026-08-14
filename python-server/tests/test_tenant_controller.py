"""Covers the controller layer: orchestration and the ORM-to-schema boundary."""
import uuid

from app.controllers.tenant import TenantController
from app.schemas.tenant import TenantResponse

TENANT_ID = uuid.UUID('6901e1d7-1b6b-4001-8f13-f4b7e29ef1f1')


class Row:
    """A stored row as the ORM hands it over: plain attributes, no behaviour."""

    def __init__(self, tenant_id, tenant_name):
        self.tenant_id = tenant_id
        self.tenant_name = tenant_name


class StubTenantRepository:
    def __init__(self, rows):
        self._rows = rows
        self.calls = 0

    async def list_all(self):
        self.calls += 1
        return self._rows


async def test_rows_are_converted_to_the_response_schema():
    """No ORM object may escape the controller: detached rows can fire a query on
    attribute access long after the session that owned them has closed."""
    controller = TenantController(StubTenantRepository([Row(TENANT_ID, 'microsoft')]))

    tenants = await controller.list_tenants()

    assert all(isinstance(tenant, TenantResponse) for tenant in tenants)
    assert tenants[0].tenant_id == TENANT_ID
    assert tenants[0].tenant_name == 'microsoft'


async def test_repository_order_is_preserved():
    rows = [Row(uuid.uuid4(), name) for name in ('microsoft', 'amazon', 'google')]
    controller = TenantController(StubTenantRepository(rows))

    tenants = await controller.list_tenants()

    assert [tenant.tenant_name for tenant in tenants] == ['microsoft', 'amazon', 'google']


async def test_no_rows_yields_an_empty_list():
    controller = TenantController(StubTenantRepository([]))
    assert await controller.list_tenants() == []


async def test_reads_the_repository_once():
    repository = StubTenantRepository([Row(TENANT_ID, 'microsoft')])
    controller = TenantController(repository)

    await controller.list_tenants()

    assert repository.calls == 1
