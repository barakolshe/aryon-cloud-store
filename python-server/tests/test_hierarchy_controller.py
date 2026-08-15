"""Covers the tree assembly in app/lib/hierarchy.py.

No database, no event loop, and no repository: `assemble_tree` is a static method over the
rows a repository returns, which is what makes the interesting cases -- ordering, a leaf, a
hierarchy deeper than Python's recursion limit -- cheap to state. Reached through the class
rather than an instance, since it depends on nothing an instance holds.
"""
import json

from hierarchy_fixtures import SubtreeRow, load_fixture, subtree_rows

from app.lib.hierarchy import HierarchyService


def test_assembles_a_stored_hierarchy_back_into_the_posted_shape():
    """The acceptance criterion, at the level where the nesting is decided: flatten a
    sample hierarchy the way the database stores it, and the assembled tree serialises
    back to the file byte for byte."""
    hierarchy = load_fixture('5')

    assembled = HierarchyService.assemble_tree(subtree_rows(hierarchy, None))

    assert json.dumps(assembled.model_dump(mode='json'), sort_keys=True) == json.dumps(
        hierarchy, sort_keys=True
    )


def test_a_leaf_carries_an_explicit_empty_children_list():
    assembled = HierarchyService.assemble_tree(subtree_rows(load_fixture('1'), None))

    assert assembled.model_dump(mode='json') == {
        'id': 142,
        'type': 'management_group',
        'children': [],
    }


def test_no_rows_means_no_such_node():
    """A stored node always has its own depth-0 closure row, so an empty read cannot mean
    "a node with nothing under it" -- it is what the route turns into a 404."""
    assert HierarchyService.assemble_tree([]) is None


def test_children_follow_the_order_of_the_rows():
    """Child order is not arbitrary: run_tests.py compares with json.dumps(sort_keys=True),
    which sorts dict keys but leaves list order alone, and every fixture lists children in
    ascending id order. The query supplies that order; this is the half that keeps it."""
    rows = [
        SubtreeRow(1, 'management_group', None, 0),
        SubtreeRow(2, 'subscription', 1, 1),
        SubtreeRow(3, 'subscription', 1, 1),
        SubtreeRow(4, 'subscription', 1, 1),
    ]

    assembled = HierarchyService.assemble_tree(rows)

    assert [child.id for child in assembled.children] == [2, 3, 4]


def test_siblings_under_different_parents_stay_with_their_own_parent():
    """Rows at one depth are interleaved across parents -- ordering by id says nothing
    about which parent a node belongs to, so attaching is by parent_id, not by position."""
    rows = [
        SubtreeRow(1, 'management_group', None, 0),
        SubtreeRow(2, 'subscription', 1, 1),
        SubtreeRow(3, 'subscription', 1, 1),
        SubtreeRow(4, 'resource_group', 3, 2),
        SubtreeRow(5, 'resource_group', 2, 2),
        SubtreeRow(6, 'resource_group', 3, 2),
    ]

    assembled = HierarchyService.assemble_tree(rows)

    children = {child.id: [grandchild.id for grandchild in child.children] for child in assembled.children}
    assert children == {2: [5], 3: [4, 6]}


def test_a_hierarchy_deeper_than_the_recursion_limit_assembles():
    """`sys.setrecursionlimit` defaults to 1000, so a recursive assembly would raise here.
    The tree is walked iteratively for the same reason it is built that way."""
    depth = 1500
    rows = [SubtreeRow(1, 'management_group', None, 0)]
    rows.extend(
        SubtreeRow(node_id, 'management_group', node_id - 1, node_id - 1)
        for node_id in range(2, depth + 1)
    )

    assembled = HierarchyService.assemble_tree(rows)

    walked = 0
    node = assembled
    while node is not None:
        walked += 1
        node = node.children[0] if node.children else None
    assert walked == depth
