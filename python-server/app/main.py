"""The ASGI entrypoint: builds the app and registers routers, and nothing else.

No engine and no SQL live here. Each router carries its own wiring, so adding an
endpoint means adding a module and one `include_router` line.
"""
from fastapi import FastAPI

from app.routes import health, tenants


def create_app() -> FastAPI:
    """Assemble the application."""
    app = FastAPI()
    app.include_router(health.router)
    app.include_router(tenants.router)
    return app


# The Dockerfile serves `uvicorn app.main:app`, so the module exposes one built instance
# alongside the factory that tests call to get an isolated app.
app = create_app()
