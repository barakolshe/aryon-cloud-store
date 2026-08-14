"""The response contract for GET /health."""
from pydantic import BaseModel


class HealthStatus(BaseModel):
    """Body of GET /health.

    A liveness signal only: it reports that the process is serving requests, and says
    nothing about whether Postgres is reachable. Keeping the two apart is the point --
    an orchestrator restarting the API because the database blinked helps nobody.
    """

    status: str
