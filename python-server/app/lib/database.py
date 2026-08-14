"""Engine, session factory, and the per-request session dependency.

Everything that talks to Postgres goes through here, so no other module has to know
how the connection is built.
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# `create_async_engine` opens no connection -- it only builds the pool, which dials out
# lazily on the first query. That is what lets the process start, and /health answer 200,
# while Postgres is down.
engine = create_async_engine(settings.database_url)

# expire_on_commit=False: after a controller commits, the rows it holds stay readable.
# The default expires them, so converting a row to a response schema would fire a lazy
# refresh -- an extra round trip at best, and an error once the session has closed.
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one session for the life of a request, then close it.

    Deliberately does not commit: the transaction boundary belongs to the controller,
    which is the only layer that knows whether a unit of work succeeded. Anything left
    uncommitted when the session closes is rolled back, so a failed request cannot leak
    a half-written change.
    """
    async with session_factory() as session:
        yield session
