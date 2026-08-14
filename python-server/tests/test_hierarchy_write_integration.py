"""Covers POST /hierarchy end to end, against the compose Postgres.

Every case here goes through the real endpoint and then checks the database directly,
because the thing most worth testing is invisible from the API: `nodes.parent_id` holds the
shape, `node_closure` is derived from it, and no constraint can check that the two agree.
`assert_representations_agree` is therefore run after every mutation rather than only where
a test is about consistency -- a write path that corrupts the closure while still answering
GET correctly is exactly the failure the schema cannot catch on its own.

State is built by posting rather than by seeding, since what these tests are about is what
a *sequence* of posts does to stored state.
"""
import json

import pytest
from hierarchy_fixtures import ClosureRow, closure_from_parent_edges, load_fixture
from sqlalchemy import text

FIXTURE_NAMES = ['1', '2', '3', '4', '5', '6']


async def stored_nodes(engine):
    """`{node_id: parent_id}` for everything in `nodes`."""
    async with engine.connect() as connection:
        result = await connection.execute(text('SELECT id, parent_id FROM nodes'))
        return {row.id: row.parent_id for row in result}


async def stored_closure(engine):
    async with engine.connect() as connection:
        result = await connection.execute(
            text('SELECT ancestor_id, descendant_id, depth FROM node_closure')
        )
        return {ClosureRow(*row) for row in result}


async def assert_representations_agree(engine):
    """The post-condition the write path exists to maintain.

    Three claims, strongest first: the stored closure is exactly the closure implied by
    `nodes.parent_id`; every node's parent column matches its depth-1 closure ancestor; and
    no node has two depth-1 ancestors. The first subsumes the other two, which are asserted
    separately anyway so a failure says which way the two representations drifted.
    """
    parents = await stored_nodes(engine)
    closure = await stored_closure(engine)

    assert closure == closure_from_parent_edges(parents)

    parent_by_closure = {}
    for row in closure:
        if row.depth == 1:
            assert row.descendant_id not in parent_by_closure, (
                f'node {row.descendant_id} has two depth-1 ancestors'
            )
            parent_by_closure[row.descendant_id] = row.ancestor_id

    assert parent_by_closure == {
        node_id: parent for node_id, parent in parents.items() if parent is not None
    }


async def post(client, hierarchy):
    response = await client.post('/hierarchy', json=hierarchy)
    assert response.status_code == 200, response.text
    return response


async def get(client, node_id):
    return await client.get(f'/hierarchy/{node_id}')


def tree(node_id, node_type, children):
    return {'id': node_id, 'type': node_type, 'children': children}


async def test_the_sample_hierarchies_replay_in_order(database_engine, client):
    """The assignment's own measure, in process: post each sample file in turn and fetch it
    back. Order matters -- fixture 3 removes nodes fixture 2 added, 4 moves one between
    parents, and 6 re-parents node 7 into a tree of its own, so each file is a different
    kind of change applied to what the previous one left behind."""
    for name in FIXTURE_NAMES:
        hierarchy = load_fixture(name)

        await post(client, hierarchy)
        fetched = await get(client, hierarchy['id'])

        assert json.dumps(fetched.json(), sort_keys=True) == json.dumps(
            hierarchy, sort_keys=True
        ), f'fixture {name} did not come back as posted'
        await assert_representations_agree(database_engine)


async def test_the_response_body_is_the_stored_subtree(database_engine, client):
    """200 carries what was stored, not an echo of the request -- normalised child ordering
    included, which is the part a client cannot derive for itself."""
    response = await post(
        client,
        tree(
            1,
            'management_group',
            [
                tree(30, 'subscription', []),
                tree(10, 'subscription', []),
                tree(20, 'subscription', []),
            ],
        ),
    )

    assert [child['id'] for child in response.json()['children']] == [10, 20, 30]
    assert response.json() == (await get(client, 1)).json()


async def test_reposting_an_unchanged_payload_changes_nothing(database_engine, client):
    """Idempotence, stated over the stored rows rather than over the response: re-posting
    must not accumulate closure rows, and must not renumber or re-parent anything."""
    await post(client, load_fixture('5'))
    nodes_before = await stored_nodes(database_engine)
    closure_before = await stored_closure(database_engine)

    await post(client, load_fixture('5'))

    assert await stored_nodes(database_engine) == nodes_before
    assert await stored_closure(database_engine) == closure_before
    await assert_representations_agree(database_engine)


async def test_removing_a_node_deletes_its_whole_subtree(database_engine, client):
    """Fixture 2 -> 3 drops node 6, which has child 7. Both go, and neither leaves a closure
    row behind -- as ancestor or as descendant."""
    await post(client, load_fixture('2'))
    assert 7 in await stored_nodes(database_engine)

    await post(client, load_fixture('3'))

    parents = await stored_nodes(database_engine)
    assert 6 not in parents
    assert 7 not in parents

    closure = await stored_closure(database_engine)
    orphaned = [row for row in closure if {row.ancestor_id, row.descendant_id} & {6, 7}]
    assert orphaned == []
    await assert_representations_agree(database_engine)


async def test_moving_a_node_preserves_its_subtree(database_engine, client):
    """Fixture 3 -> 4 moves node 4 from under 3 to under 1. Its child 5 comes with it, and
    the closure rows that place 5 relative to the tree above are rewritten to match."""
    await post(client, load_fixture('3'))

    await post(client, load_fixture('4'))

    assert (await stored_nodes(database_engine))[4] == 1
    closure = await stored_closure(database_engine)
    assert ClosureRow(4, 5, 1) in closure, 'node 5 lost its parent in the move'
    assert ClosureRow(1, 5, 2) in closure, 'node 5 was not re-hung under the new grandparent'
    assert ClosureRow(3, 5, 3) not in closure, 'node 5 kept a stale ancestor'
    await assert_representations_agree(database_engine)


async def test_a_node_moved_out_of_a_subtree_deleted_in_the_same_request(
    database_engine, client
):
    """The ordering case. Node 3 moves up to the root while node 2 -- its parent, and the
    only thing between them -- is removed by the same payload. `nodes.parent_id` is ON
    DELETE RESTRICT, so deleting 2 before 3 is re-parented is refused for orphaning it;
    the write path upserts first, which clears the reference before the delete runs.
    """
    await post(client, tree(1, 'management_group', [tree(2, 'subscription', [
        tree(3, 'resource_group', []),
    ])]))

    await post(client, tree(1, 'management_group', [tree(3, 'resource_group', [])]))

    assert (await get(client, 1)).json() == tree(1, 'management_group', [
        tree(3, 'resource_group', []),
    ])
    assert 2 not in await stored_nodes(database_engine)
    await assert_representations_agree(database_engine)


async def test_a_node_moved_in_from_another_tree_loses_children_the_payload_omits(
    database_engine, client
):
    """The reason the delete set unions over every payload id rather than only the root's
    descendants. Node 200 arrives from a different tree still carrying child 300, which the
    new payload does not mention -- scoping the delete to the destination root's descendants
    would leave 300 stored and unreachable."""
    await post(client, tree(100, 'management_group', [tree(200, 'subscription', [
        tree(300, 'resource_group', []),
    ])]))

    await post(client, tree(900, 'management_group', [tree(200, 'subscription', [])]))

    assert (await get(client, 900)).json() == tree(900, 'management_group', [
        tree(200, 'subscription', []),
    ])
    assert (await get(client, 100)).json() == tree(100, 'management_group', [])
    assert 300 not in await stored_nodes(database_engine)
    await assert_representations_agree(database_engine)


async def test_posting_a_subtree_keeps_it_attached_to_the_tree_above_it(
    database_engine, client
):
    """A payload names no parent for its own root. Posting an interior node must not turn it
    into a root -- the captured ancestors go back into the closure at their old depths."""
    await post(client, load_fixture('5'))

    await post(client, tree(3, 'subscription', [tree(8, 'resource_group', [])]))

    assert (await stored_nodes(database_engine))[3] == 1
    closure = await stored_closure(database_engine)
    assert ClosureRow(1, 3, 1) in closure
    assert ClosureRow(1, 8, 2) in closure
    await assert_representations_agree(database_engine)


@pytest.mark.parametrize('missing_from_payload', [9, 10])
async def test_posting_a_subtree_removes_what_it_does_not_list(
    database_engine, client, missing_from_payload
):
    """The governing rule, at the level of a single node: the payload is authoritative for
    everything beneath every node it mentions."""
    await post(client, load_fixture('5'))

    await post(client, tree(3, 'subscription', [tree(8, 'resource_group', [])]))

    assert missing_from_payload not in await stored_nodes(database_engine)
    await assert_representations_agree(database_engine)


async def test_an_id_repeated_in_one_payload_is_rejected(database_engine, client):
    response = await client.post(
        '/hierarchy',
        json=tree(
            1,
            'management_group',
            [tree(2, 'subscription', []), tree(2, 'subscription', [])],
        ),
    )

    assert response.status_code == 409
    assert '2' in response.json()['detail']
    assert await stored_nodes(database_engine) == {}, 'a rejected payload wrote rows'


async def test_a_payload_containing_its_own_ancestor_is_rejected(database_engine, client):
    """Posting node 3 with node 1 underneath it, while 1 is already above 3, would make each
    the other's ancestor. Caught before anything is written."""
    await post(client, load_fixture('5'))
    nodes_before = await stored_nodes(database_engine)

    response = await client.post(
        '/hierarchy',
        json=tree(3, 'subscription', [tree(1, 'management_group', [])]),
    )

    assert response.status_code == 409
    assert 'cycle' in response.json()['detail']
    assert await stored_nodes(database_engine) == nodes_before
    await assert_representations_agree(database_engine)


async def test_a_rejected_payload_names_no_sql(database_engine, client):
    """Whatever the error body grows into, it does not hand the caller the query."""
    response = await client.post(
        '/hierarchy',
        json=tree(1, 'management_group', [tree(1, 'subscription', [])]),
    )

    body = response.text.lower()
    assert 'insert' not in body
    assert 'node_closure' not in body
