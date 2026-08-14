"""Covers app/models/node.py and app/models/node_closure.py.

These assertions are about `Base.metadata` -- the description of the schema that env.py
hands Alembic, and that the initial migration mirrors by hand. A model that drifts from the
migration produces a phantom autogenerate diff on the next schema change, so the shape is
worth pinning here as well as in the DDL assertions in test_migrations.py.

Nothing connects to Postgres: a Table object is built at import time.
"""
import importlib

import pytest
from sqlalchemy import BigInteger, Integer

TYPE_LABELS = ['management_group', 'subscription', 'resource_group']


@pytest.fixture
def nodes():
    return importlib.import_module('app.models.node').Node.__table__


@pytest.fixture
def closure(nodes):
    """Depends on `nodes` because the closure table's foreign keys are strings until the
    table they name is on the same metadata -- the same reason env.py imports both."""
    return importlib.import_module('app.models.node_closure').NodeClosure.__table__


def indexes_by_name(table):
    return {index.name: index for index in table.indexes}


def constraint_named(table, name):
    return next(constraint for constraint in table.constraints if constraint.name == name)


def test_tables_are_named_as_the_migration_names_them(nodes, closure):
    assert nodes.name == 'nodes'
    assert closure.name == 'node_closure'


def test_both_tables_reach_the_metadata_alembic_diffs():
    """env.py imports these two modules for exactly this side effect. A model missing from
    that import line is invisible to autogenerate, which then writes a migration that drops
    its table."""
    base = importlib.import_module('app.models.base').Base
    importlib.import_module('app.models.node')
    importlib.import_module('app.models.node_closure')

    assert set(base.metadata.tables) == {'nodes', 'node_closure'}


def test_node_id_is_a_client_supplied_bigint_key(nodes):
    """autoincrement would compile the column to BIGSERIAL and hand Postgres a sequence
    that every insert overrides."""
    assert isinstance(nodes.c.id.type, BigInteger)
    assert nodes.c.id.primary_key
    assert nodes.c.id.autoincrement is False


def test_node_type_is_a_native_enum_of_the_api_values(nodes):
    """Without values_callable in the model, SQLAlchemy stores enum member names and the
    Postgres labels come out uppercased, so every payload value fails to match."""
    assert nodes.c.type.type.name == 'node_type'
    assert nodes.c.type.type.enums == TYPE_LABELS
    assert nodes.c.type.nullable is False


def test_parent_id_is_a_nullable_self_reference(nodes):
    """NULL for a root, and a column holds one value -- which is what makes "at most one
    parent" structural rather than something the write path has to be trusted with."""
    foreign_key = next(iter(nodes.c.parent_id.foreign_keys))

    assert nodes.c.parent_id.nullable is True
    assert foreign_key.column.table.name == 'nodes'
    assert foreign_key.column.name == 'id'


def test_deleting_a_node_out_from_under_its_children_is_refused(nodes):
    """RESTRICT, not CASCADE. A cascade here is recursive, so one stray DELETE would take an
    unbounded part of the hierarchy with it and report a single row deleted while doing it.
    The write path names a whole subtree in one DELETE, which RESTRICT allows."""
    foreign_key = next(iter(nodes.c.parent_id.foreign_keys))

    assert foreign_key.ondelete == 'RESTRICT'


def test_parent_id_is_indexed_for_the_constraint_check(nodes):
    """Postgres does not index the referencing side of a foreign key on its own, so every
    delete would sequentially scan the table looking for children without this."""
    index = indexes_by_name(nodes)['ix_nodes_parent_id']

    assert [column.name for column in index.columns] == ['parent_id']
    assert not index.unique


def test_closure_is_keyed_by_the_ancestor_descendant_pair(closure):
    assert [column.name for column in closure.primary_key] == ['ancestor_id', 'descendant_id']
    assert isinstance(closure.c.depth.type, Integer)
    assert closure.c.depth.nullable is False


def test_closure_rows_are_cascaded_away_with_their_node(closure):
    """The write path deletes from `nodes` only; the closure rows have to follow, or the
    next read joins against ids that no longer exist."""
    targets = {
        foreign_key.column.table.name: foreign_key.ondelete
        for foreign_key in closure.foreign_keys
    }
    assert targets == {'nodes': 'CASCADE'}
    assert len(closure.foreign_keys) == 2


def test_depth_zero_is_allowed_and_negative_depth_is_not(closure):
    """>= 0, not > 0: every node carries a depth-0 row pointing at itself."""
    constraint = constraint_named(closure, 'ck_node_closure_depth_non_negative')
    assert str(constraint.sqltext) == 'depth >= 0'


def test_descendant_index_covers_the_read_path(closure):
    index = indexes_by_name(closure)['ix_closure_descendant_depth']
    assert [column.name for column in index.columns] == ['descendant_id', 'depth']
    assert not index.unique


def test_the_derived_closure_cannot_record_two_parents_either(closure):
    """nodes.parent_id is the single-parent guarantee; this index checks the derived copy
    agrees that there is only one, by rejecting a second depth-1 row for the same node."""
    index = indexes_by_name(closure)['uq_node_single_parent']
    assert [column.name for column in index.columns] == ['descendant_id']
    assert index.unique
    assert str(index.dialect_options['postgresql']['where']) == 'depth = 1'
