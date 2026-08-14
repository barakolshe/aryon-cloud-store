"""Covers alembic.ini, alembic/env.py and the initial migration -- without a database.

`alembic upgrade head --sql` runs env.py's offline branch, which configures the migration
context from a dialect instead of a connection and writes the DDL to a buffer. That
exercises the real migration code, so the statements asserted below are the ones Postgres
would receive. Applying them for real stays a psql check against the compose stack.
"""
import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

# env.py imports app.config, which reads the environment. Offline mode opens no connection,
# so this URL only has to name a dialect Alembic can compile against.
DATABASE_URL = 'postgresql://aryon:aryon@postgres:5432/aryondb'
INI_PATH = Path(__file__).resolve().parents[1] / 'alembic.ini'


@pytest.fixture
def alembic_config(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', DATABASE_URL)
    return Config(str(INI_PATH), output_buffer=io.StringIO())


def emitted(alembic_config):
    """The buffered DDL as one whitespace-normalised line, so assertions can be written the
    way the statement reads rather than the way SQLAlchemy wraps it."""
    return ' '.join(alembic_config.output_buffer.getvalue().split())


@pytest.fixture
def upgrade_sql(alembic_config):
    command.upgrade(alembic_config, 'head', sql=True)
    return emitted(alembic_config)


@pytest.fixture
def downgrade_sql(alembic_config):
    # Offline downgrade needs an explicit range: with no database to ask, Alembic cannot
    # work out where the schema currently stands.
    command.downgrade(alembic_config, 'head:base', sql=True)
    return emitted(alembic_config)


def test_there_is_one_head_and_it_starts_from_an_empty_database(alembic_config):
    script = ScriptDirectory.from_config(alembic_config)
    heads = script.get_heads()

    assert len(heads) == 1
    assert script.get_revision(heads[0]).down_revision is None


def test_the_enum_is_created_with_the_values_the_api_speaks(upgrade_sql):
    assert (
        "CREATE TYPE node_type AS ENUM "
        "('management_group', 'subscription', 'resource_group')"
    ) in upgrade_sql


def test_node_ids_are_stored_as_given(upgrade_sql):
    """BIGINT, not BIGSERIAL: ids come from the client and no sequence generates them."""
    assert 'id BIGINT NOT NULL' in upgrade_sql
    assert 'SERIAL' not in upgrade_sql
    assert 'CREATE SEQUENCE' not in upgrade_sql


def test_nodes_table_is_keyed_by_id(upgrade_sql):
    assert 'CREATE TABLE nodes' in upgrade_sql
    assert 'type node_type NOT NULL' in upgrade_sql
    assert 'PRIMARY KEY (id)' in upgrade_sql


def test_closure_rows_cascade_from_their_node(upgrade_sql):
    assert 'CREATE TABLE node_closure' in upgrade_sql
    assert 'PRIMARY KEY (ancestor_id, descendant_id)' in upgrade_sql
    assert 'FOREIGN KEY(ancestor_id) REFERENCES nodes (id) ON DELETE CASCADE' in upgrade_sql
    assert 'FOREIGN KEY(descendant_id) REFERENCES nodes (id) ON DELETE CASCADE' in upgrade_sql


def test_depth_may_be_zero_but_not_negative(upgrade_sql):
    assert 'CONSTRAINT ck_node_closure_depth_non_negative CHECK (depth >= 0)' in upgrade_sql


def test_both_closure_indexes_are_created(upgrade_sql):
    assert 'CREATE INDEX ix_closure_descendant_depth ON node_closure (descendant_id, depth)' in upgrade_sql
    assert (
        'CREATE UNIQUE INDEX uq_node_single_parent ON node_closure (descendant_id) '
        'WHERE depth = 1'
    ) in upgrade_sql


def test_downgrade_takes_the_enum_with_it(downgrade_sql):
    """DROP TABLE leaves the type behind, and the next `upgrade head` then fails on
    "type node_type already exists"."""
    assert 'DROP TABLE node_closure' in downgrade_sql
    assert 'DROP TABLE nodes' in downgrade_sql
    assert 'DROP TYPE node_type' in downgrade_sql
