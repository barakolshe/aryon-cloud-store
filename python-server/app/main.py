"""The ASGI entrypoint: builds the app, registers routers and error handlers, nothing else.

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
from app.errors import register_error_handlers
from app.routes import health, hierarchy


def create_app() -> FastAPI:
    """Assemble the application.

    The handlers go on before the routers so that the error contract is a property of the
    app rather than of any endpoint: a route raises, and what the caller sees is decided
    once, in app/errors.py, for every route that will ever raise the same thing.
    """
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(hierarchy.router)
    return app


# The Dockerfile serves `uvicorn app.main:app`, so the module exposes one built instance
# alongside the factory that tests call to get an isolated app.
app = create_app()
