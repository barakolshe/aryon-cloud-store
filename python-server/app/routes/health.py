"""HTTP surface for the liveness check."""
from fastapi import APIRouter

from app.schemas.health import HealthStatus

router = APIRouter()


@router.get('/health', response_model=HealthStatus)
async def health() -> HealthStatus:
    """Report that the process is serving requests.

    Depends on nothing -- no session, no query -- so it answers 200 even with Postgres
    stopped. That is the whole point: it distinguishes "the server is up" from "the
    database is reachable", which are different failures needing different responses.
    """
    return HealthStatus(status='ok')
