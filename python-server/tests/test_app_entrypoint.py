"""Covers the uvicorn entrypoint the Dockerfile serves: `uv run uvicorn app.main:app`."""
import importlib
import sys

import pytest
from fastapi import FastAPI

RELOADABLE_MODULES = ('app.main', 'app.core.config')
DATABASE_URL = 'postgresql://aryon:aryon@postgres:5432/aryondb'


@pytest.fixture(autouse=True)
def forget_app_modules():
    """Import the entrypoint fresh per test: it builds its engine at import time
    from the settings instance, which is itself built once per import."""
    for name in RELOADABLE_MODULES:
        sys.modules.pop(name, None)
    yield
    for name in RELOADABLE_MODULES:
        sys.modules.pop(name, None)


def import_main(monkeypatch, database_url):
    monkeypatch.setenv('DATABASE_URL', database_url)
    return importlib.import_module('app.main')


def test_app_attribute_is_an_asgi_app(monkeypatch):
    module = import_main(monkeypatch, DATABASE_URL)
    assert isinstance(module.app, FastAPI)


def test_tenants_route_is_registered(monkeypatch):
    module = import_main(monkeypatch, DATABASE_URL)
    assert '/tenants' in {route.path for route in module.app.routes}


def test_plain_postgresql_url_is_driven_by_async_psycopg(monkeypatch):
    module = import_main(monkeypatch, DATABASE_URL)
    assert module.engine.url.drivername == 'postgresql+psycopg'


def test_missing_database_url_fails_at_import(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        importlib.import_module('app.main')
