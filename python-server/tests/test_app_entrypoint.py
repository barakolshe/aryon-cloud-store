"""Covers the uvicorn entrypoint the Dockerfile serves: `uv run uvicorn app.main:app`.

The per-test import isolation these tests rely on comes from conftest.py.
"""
import importlib

import pytest
from fastapi import FastAPI

DATABASE_URL = 'postgresql://aryon:aryon@postgres:5432/aryondb'


def import_main(monkeypatch, database_url):
    monkeypatch.setenv('DATABASE_URL', database_url)
    return importlib.import_module('app.main')


def test_app_attribute_is_an_asgi_app(monkeypatch):
    module = import_main(monkeypatch, DATABASE_URL)
    assert isinstance(module.app, FastAPI)


def test_create_app_builds_a_fresh_instance(monkeypatch):
    module = import_main(monkeypatch, DATABASE_URL)
    assert isinstance(module.create_app(), FastAPI)
    assert module.create_app() is not module.app


def test_registered_routes(monkeypatch):
    """Read the paths off the OpenAPI schema rather than app.routes: since FastAPI
    0.141 `include_router` leaves an `_IncludedRouter` wrapper there, which carries no
    `.path`. The schema is the public view of what the app actually serves."""
    module = import_main(monkeypatch, DATABASE_URL)
    assert set(module.app.openapi()['paths']) == {'/health'}


def test_entrypoint_holds_no_engine(monkeypatch):
    """The engine lives in app.database now. Guards the split from quietly regressing."""
    module = import_main(monkeypatch, DATABASE_URL)
    assert not hasattr(module, 'engine')


def test_missing_database_url_fails_at_import(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        importlib.import_module('app.main')
