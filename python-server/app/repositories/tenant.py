"""All SQL against the `tenants` table. Nothing above this layer issues a query."""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantRepository:
    """Reads tenants through the session the request was given.

    Takes the session rather than the engine: the caller decides how wide the unit of
    work is, so a repository can never commit behind a controller's back.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> Sequence[Tenant]:
        """Every tenant, in whatever order Postgres returns them.

        No ORDER BY on purpose. The raw SQL this replaced had none either, so the rows
        arrive in the same order and GET /tenants keeps returning byte-identical JSON --
        adding one here would look tidier and silently change the response.
        """
        result = await self._session.execute(select(Tenant))
        return result.scalars().all()
