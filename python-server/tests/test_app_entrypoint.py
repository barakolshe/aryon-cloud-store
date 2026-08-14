"""Covers the uvicorn entrypoint the Dockerfile serves: `uv run uvicorn app:app`."""
import importlib

import pytest
from fastapi import FastAPI


def load_app_module(monkeypatch, database_url):
    monkeypatch.setenv('DATABASE_URL', database_url)
    import app
    return importlib.reload(app)


def test_app_attribute_is_an_asgi_app(monkeypatch):
    module = load_app_module(monkeypatch, 'postgresql://aryon:aryon@postgres:5432/aryondb')
    assert isinstance(module.app, FastAPI)


def test_tenants_route_is_registered(monkeypatch):
    module = load_app_module(monkeypatch, 'postgresql://aryon:aryon@postgres:5432/aryondb')
    assert '/tenants' in {route.path for route in module.app.routes}


def test_plain_postgresql_url_is_driven_by_async_psycopg(monkeypatch):
    module = load_app_module(monkeypatch, 'postgresql://aryon:aryon@postgres:5432/aryondb')
    assert module.engine.url.drivername == 'postgresql+psycopg'


def test_missing_database_url_fails_at_import(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    import app
    with pytest.raises(KeyError):
        importlib.reload(app)
