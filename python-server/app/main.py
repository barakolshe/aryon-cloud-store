"""The ASGI entrypoint: builds the app and registers routers, and nothing else.

No engine and no SQL are defined here. Each router carries its own wiring, so adding an
endpoint means adding a module and one `include_router` line.
"""
from fastapi import FastAPI

# Imported for its side effect, not its contents: loading app.database builds the engine
# from app.config, which validates DATABASE_URL. That makes a missing or malformed URL a
# startup failure rather than a 500 on whichever request first needs the database. The
# routers below reach app.database on their own once endpoints query it, but relying on
# that would tie fail-fast startup to which endpoints happen to exist today.
from app import database  # noqa: F401
from app.routes import health


def create_app() -> FastAPI:
    """Assemble the application."""
    app = FastAPI()
    app.include_router(health.router)
    return app


# The Dockerfile serves `uvicorn app.main:app`, so the module exposes one built instance
# alongside the factory that tests call to get an isolated app.
app = create_app()
