"""Covers app/core/config.py -- the only module allowed to read the environment."""
import importlib
import sys

import pytest
from pydantic import ValidationError

CONFIG_MODULE = 'app.core.config'
PLAIN_URL = 'postgresql://aryon:aryon@postgres:5432/aryondb'
ASYNC_URL = 'postgresql+psycopg://aryon:aryon@postgres:5432/aryondb'


@pytest.fixture(autouse=True)
def forget_config_module():
    """Import the config module fresh per test, so its module-level `settings`
    is built from that test's environment rather than an earlier test's."""
    sys.modules.pop(CONFIG_MODULE, None)
    yield
    sys.modules.pop(CONFIG_MODULE, None)


def import_config(monkeypatch, database_url):
    monkeypatch.setenv('DATABASE_URL', database_url)
    return importlib.import_module(CONFIG_MODULE)


def test_settings_requires_database_url(monkeypatch):
    config = import_config(monkeypatch, PLAIN_URL)
    monkeypatch.delenv('DATABASE_URL')
    with pytest.raises(ValidationError):
        config.Settings()


def test_settings_rejects_an_empty_database_url(monkeypatch):
    config = import_config(monkeypatch, PLAIN_URL)
    monkeypatch.setenv('DATABASE_URL', '')
    with pytest.raises(ValidationError):
        config.Settings()


def test_plain_postgresql_url_is_rewritten_to_the_async_dialect(monkeypatch):
    config = import_config(monkeypatch, PLAIN_URL)
    assert config.Settings().database_url == ASYNC_URL


def test_async_dialect_url_is_left_alone(monkeypatch):
    config = import_config(monkeypatch, ASYNC_URL)
    assert config.Settings().database_url == ASYNC_URL


def test_query_parameters_survive_the_rewrite(monkeypatch):
    config = import_config(monkeypatch, f'{PLAIN_URL}?sslmode=disable')
    assert config.Settings().database_url == f'{ASYNC_URL}?sslmode=disable'


def test_module_level_settings_reads_the_environment(monkeypatch):
    config = import_config(monkeypatch, PLAIN_URL)
    assert config.settings.database_url == ASYNC_URL


def test_import_without_database_url_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        importlib.import_module(CONFIG_MODULE)
