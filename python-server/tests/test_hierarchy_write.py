"""Covers the two pure halves of the write path in app/controllers/hierarchy.py.

No database and no event loop. `flatten_payload` and `closure_rows` are what decide the
rows the transaction writes, so getting them under test without a connection is what makes
the interesting cases -- ordering, depth arithmetic, a subtree grafted under existing
ancestors -- cheap enough to state exhaustively.
"""
import pytest
from hierarchy_fixtures import closure_rows as closure_rows_of_fixture
from hierarchy_fixtures import load_fixture

from app.controllers.hierarchy import (
    ClosureRow,
    InvalidHierarchy,
    closure_rows,
    flatten_payload,
)
from app.schemas.node import HierarchyNode, NodeType

FIXTURE_NAMES = ['1', '2', '3', '4', '5', '6']


def payload(name):
    return HierarchyNode.model_validate(load_fixture(name))


def construct_chain(depth):
    """A single-file hierarchy `depth` nodes deep, built without validating.

    `model_construct` skips Pydantic's recursion guard, which refuses a nesting this deep.
    That guard is a property of the schema, not of the functions under test here.
    """
    node = HierarchyNode.model_construct(id=depth, type=NodeType.MANAGEMENT_GROUP, children=[])
    for node_id in range(depth - 1, 0, -1):
        node = HierarchyNode.model_construct(
            id=node_id, type=NodeType.MANAGEMENT_GROUP, children=[node]
        )
    return node


def test_a_payload_flattens_parents_before_children():
    """The property the closure pass and the upsert both rely on, asserted directly rather
    than inferred from the row order below: no node appears before its own parent."""
    nodes = flatten_payload(payload('5'))

    seen = set()
    for node in nodes:
        if node.parent_id is not None:
            assert node.parent_id in seen, f'node {node.id} came before its parent'
        seen.add(node.id)


def test_flattening_reports_each_node_with_its_parent_and_depth():
    nodes = flatten_payload(payload('3'))

    assert [(node.id, node.parent_id, node.depth) for node in nodes] == [
        (1, None, 0),
        (2, 1, 1),
        (3, 1, 1),
        (4, 3, 2),
        (8, 3, 2),
        (5, 4, 3),
    ]


def test_flattening_orders_each_level_by_id():
    """Siblings come out ascending whatever order the payload listed them in -- the same
    order the read path returns, so a re-post cannot reshuffle a stored tree."""
    nodes = flatten_payload(
        HierarchyNode.model_validate(
            {
                'id': 1,
                'type': 'management_group',
                'children': [
                    {'id': 30, 'type': 'subscription', 'children': []},
                    {'id': 10, 'type': 'subscription', 'children': []},
                    {'id': 20, 'type': 'subscription', 'children': []},
                ],
            }
        )
    )

    assert [node.id for node in nodes] == [1, 10, 20, 30]


def test_the_posted_root_reports_no_parent():
    """None means "the payload did not say", not "this is a root" -- the controller
    substitutes whatever the root currently hangs from."""
    nodes = flatten_payload(payload('5'))

    assert nodes[0].id == 1
    assert nodes[0].parent_id is None


def test_an_id_repeated_across_branches_is_rejected():
    """The one structural error the schema cannot catch: each branch is well-formed on its
    own, and nothing is wrong until they are looked at together."""
    duplicated = HierarchyNode.model_validate(
        {
            'id': 1,
            'type': 'management_group',
            'children': [
                {'id': 2, 'type': 'subscription', 'children': []},
                {'id': 2, 'type': 'subscription', 'children': []},
            ],
        }
    )

    with pytest.raises(InvalidHierarchy, match='2'):
        flatten_payload(duplicated)


def test_a_hierarchy_deeper_than_the_recursion_limit_flattens():
    """`sys.setrecursionlimit` defaults to 1000, so a recursive flatten would raise here.
    The payload is walked iteratively for the same reason `assemble_tree` walks the rows
    that way.

    Built with `model_construct` rather than `model_validate` because Pydantic's own
    recursion guard rejects a nesting this deep during validation -- a separate limit, owned
    by the schema, and not the property under test. Constructing skips validation, which is
    what puts a tree of this shape in front of `flatten_payload` at all.
    """
    depth = 1500

    nodes = flatten_payload(construct_chain(depth))

    assert len(nodes) == depth
    assert nodes[-1].depth == depth - 1


def test_closure_rows_handle_a_hierarchy_deeper_than_the_recursion_limit():
    """The other half: the closure pass carries an ancestor list forward rather than walking
    up from each node, so it neither recurses nor re-walks."""
    depth = 1200

    rows = closure_rows(flatten_payload(construct_chain(depth)), [])

    # A chain of n nodes has one row per (ancestor, descendant) pair plus one self-row each.
    assert len(rows) == depth * (depth + 1) // 2
    assert ClosureRow(1, depth, depth - 1) in rows


@pytest.mark.parametrize('name', FIXTURE_NAMES)
def test_a_root_payload_produces_exactly_the_closure_of_its_own_shape(name):
    """Cross-checked against `hierarchy_fixtures.closure_rows`, which walks the nesting
    directly. Two independent derivations of the same set: this one from the flattened rows
    and a carried ancestor list, that one from the nesting itself."""
    rows = closure_rows(flatten_payload(payload(name)), [])

    assert set(rows) == set(closure_rows_of_fixture(load_fixture(name)))


@pytest.mark.parametrize('name', FIXTURE_NAMES)
def test_no_closure_row_is_emitted_twice(name):
    """`node_closure` has a primary key on (ancestor_id, descendant_id), so a duplicate here
    would be an IntegrityError at the bulk insert rather than a wrong answer."""
    rows = closure_rows(flatten_payload(payload(name)), [])

    assert len(rows) == len(set(rows))


def test_every_node_gets_a_depth_zero_self_row():
    """What makes "node exists" distinguishable from "node has no descendants", and so what
    lets the read answer 404 rather than an empty object."""
    nodes = flatten_payload(payload('5'))

    rows = closure_rows(nodes, [])

    assert {row.descendant_id for row in rows if row.depth == 0} == {node.id for node in nodes}


def test_captured_ancestors_are_offset_by_each_node_s_depth_in_the_payload():
    """Re-posting a subtree keeps it attached to the tree above it. Reaching one of the
    root's existing ancestors means walking up to the root first, so the stored depth is
    the sum of the two."""
    posted = HierarchyNode.model_validate(
        {
            'id': 10,
            'type': 'subscription',
            'children': [{'id': 20, 'type': 'resource_group', 'children': []}],
        }
    )

    rows = closure_rows(flatten_payload(posted), [(1, 2), (5, 1)])

    assert set(rows) == {
        ClosureRow(10, 10, 0),
        ClosureRow(20, 20, 0),
        ClosureRow(10, 20, 1),
        # The root, at its captured depths.
        ClosureRow(5, 10, 1),
        ClosureRow(1, 10, 2),
        # Its child, one edge further from each.
        ClosureRow(5, 20, 2),
        ClosureRow(1, 20, 3),
    }


def test_a_payload_with_no_captured_ancestors_stores_as_a_root():
    """Nothing above the posted root means no rows naming it as a descendant beyond its own
    self-row -- which is what a root looks like in the closure."""
    rows = closure_rows(flatten_payload(payload('1')), [])

    assert rows == [ClosureRow(142, 142, 0)]
