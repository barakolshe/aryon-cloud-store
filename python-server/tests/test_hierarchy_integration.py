"""Covers GET /hierarchy/{node_id} end to end, against the compose Postgres.

The whole path runs: the real router, the real controller, the real query, the migrated
schema. Rows are seeded as plain SQL rather than by posting them, so the read is tested
against what the database holds rather than against the same objects that produced it --
a bug shared by the write path and the read path would otherwise cancel out and pass.
POST has its own coverage in test_hierarchy_write_integration.py.
"""
import json

import pytest
from hierarchy_fixtures import closure_rows, load_fixture, subtree_rows
from sqlalchemy import event, text

FIXTURE_NAMES = ['1', '2', '3', '4', '5', '6']

INSERT_NODE = text(
    # The cast is not decoration: psycopg sends a Python str as text, and Postgres has no
    # implicit text -> node_type cast, so the parameter has to name its type.
    'INSERT INTO nodes (id, type, parent_id) VALUES (:id, CAST(:type AS node_type), :parent_id)'
)
INSERT_CLOSURE = text(
    'INSERT INTO node_closure (ancestor_id, descendant_id, depth)'
    ' VALUES (:ancestor_id, :descendant_id, :depth)'
)


async def seed(engine, hierarchy):
    """Store a hierarchy the way the write path will: nodes first, then the closure over them."""
    nodes = [
        {'id': row.id, 'type': row.type, 'parent_id': row.parent_id}
        for row in subtree_rows(hierarchy, None)
    ]
    closure = [row._asdict() for row in closure_rows(hierarchy)]

    async with engine.begin() as connection:
        await connection.execute(INSERT_NODE, nodes)
        await connection.execute(INSERT_CLOSURE, closure)


async def test_a_stored_hierarchy_comes_back_identical(database_engine, client):
    """The assignment's own measure: store a sample hierarchy, fetch it by root id, and the
    JSON matches the file -- key order included, since run_tests.py compares dumps."""
    hierarchy = load_fixture('5')
    await seed(database_engine, hierarchy)

    response = await client.get(f'/hierarchy/{hierarchy["id"]}')

    assert response.status_code == 200
    assert json.dumps(response.json(), sort_keys=True) == json.dumps(hierarchy, sort_keys=True)


@pytest.mark.parametrize('name', FIXTURE_NAMES)
async def test_every_sample_hierarchy_round_trips(database_engine, client, name):
    """All six against the seeded read, not only the ones the write path happens to leave
    behind. Fixture 6 is the one that matters most -- ids in the tens of thousands beside a
    single-digit one, so any accidental ordering by insertion or by string would show."""
    hierarchy = load_fixture(name)
    await seed(database_engine, hierarchy)

    response = await client.get(f'/hierarchy/{hierarchy["id"]}')

    assert json.dumps(response.json(), sort_keys=True) == json.dumps(hierarchy, sort_keys=True)


async def test_fetching_one_tree_of_several_returns_only_that_tree(database_engine, client):
    """The stored graph is a forest, and a read is rooted at the node asked for. Fixtures 5
    and 6 share no ids, so a query that forgot to filter by ancestor would return both trees'
    rows and assemble something that is neither."""
    await seed(database_engine, load_fixture('5'))
    await seed(database_engine, load_fixture('6'))

    response = await client.get('/hierarchy/1')

    assert json.dumps(response.json(), sort_keys=True) == json.dumps(
        load_fixture('5'), sort_keys=True
    )


async def test_children_come_back_in_ascending_id_order(database_engine, client):
    """Seeded in descending id order, so passing cannot be an accident of insertion order."""
    hierarchy = {
        'id': 1,
        'type': 'management_group',
        'children': [
            {'id': 30, 'type': 'subscription', 'children': []},
            {'id': 20, 'type': 'subscription', 'children': []},
            {'id': 10, 'type': 'subscription', 'children': []},
        ],
    }
    await seed(database_engine, hierarchy)

    response = await client.get('/hierarchy/1')

    assert [child['id'] for child in response.json()['children']] == [10, 20, 30]


async def test_fetching_a_leaf_returns_an_empty_children_list(database_engine, client):
    await seed(database_engine, load_fixture('5'))

    response = await client.get('/hierarchy/2')

    assert response.status_code == 200
    assert response.json() == {'id': 2, 'type': 'management_group', 'children': []}


async def test_fetching_an_interior_node_returns_only_its_own_subtree(database_engine, client):
    """A GET is rooted at the node asked for: nothing above it, and no sibling branch."""
    await seed(database_engine, load_fixture('5'))

    response = await client.get('/hierarchy/3')

    assert response.json() == {
        'id': 3,
        'type': 'subscription',
        'children': [
            {
                'id': 8,
                'type': 'resource_group',
                'children': [
                    {'id': 9, 'type': 'resource_group', 'children': []},
                    {'id': 10, 'type': 'resource_group', 'children': []},
                ],
            }
        ],
    }


async def test_an_unknown_node_is_a_404(database_engine, client):
    await seed(database_engine, load_fixture('5'))

    response = await client.get('/hierarchy/999999')

    assert response.status_code == 404
    assert response.json() == {'detail': 'No node with id 999999'}


async def test_a_404_names_no_sql(database_engine, client):
    """Whatever the error body grows into, it does not hand the caller the query."""
    response = await client.get('/hierarchy/999999')

    body = response.text.lower()
    assert 'select' not in body
    assert 'node_closure' not in body


async def test_one_select_serves_a_whole_subtree(database_engine, client):
    """The point of the closure table, asserted rather than asserted-about: the statements
    Postgres is actually sent during the request, counted."""
    await seed(database_engine, load_fixture('5'))

    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    # The seeding above has already opened and warmed a pooled connection, so nothing the
    # driver does on first connect lands in this count.
    event.listen(database_engine.sync_engine, 'before_cursor_execute', record)
    try:
        response = await client.get('/hierarchy/1')
    finally:
        event.remove(database_engine.sync_engine, 'before_cursor_execute', record)

    assert response.status_code == 200
    selects = [
        statement for statement in statements if statement.lstrip().upper().startswith('SELECT')
    ]
    assert len(selects) == 1, statements
    assert 'node_closure' in selects[0]


async def test_a_non_numeric_node_id_is_rejected_before_the_database(client):
    """`node_id: int` in the route signature, so this never reaches a query."""
    response = await client.get('/hierarchy/not-a-number')

    assert response.status_code == 422


async def test_a_node_id_too_large_for_the_column_is_rejected_before_the_database(client):
    """A Python int has no upper bound and `nodes.id` is BIGINT. Without the bound on the
    path parameter this parses as a perfectly good integer, reaches psycopg and raises
    NumericValueOutOfRange -- a 500 for a request that is simply malformed."""
    response = await client.get(f'/hierarchy/{2**63}')

    assert response.status_code == 422
