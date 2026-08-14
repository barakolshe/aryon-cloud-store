"""Orchestration for the tenant endpoints."""
from app.repositories.tenant import TenantRepository
from app.schemas.tenant import TenantResponse


class TenantController:
    """Turns stored rows into the response contract, and owns the transaction boundary.

    Listing tenants is read-only, so it opens no explicit transaction -- the session's
    implicit one is rolled back when the request-scoped session closes. Write paths added
    later wrap their work in `async with self._session.begin():` here, in the controller:
    it is the only layer that sees a whole unit of work, so it is the only one that can
    say when that work is complete. Routes and repositories never manage transactions.
    """

    def __init__(self, repository: TenantRepository) -> None:
        self._repository = repository

    async def list_tenants(self) -> list[TenantResponse]:
        """Read every tenant and convert it on the way out.

        Converting here rather than in the route keeps ORM objects below this layer.
        Left to escape, they would be detached rows whose attribute access can fire a
        query long after the session that owned them has closed.
        """
        tenants = await self._repository.list_all()
        return [TenantResponse.model_validate(tenant) for tenant in tenants]
