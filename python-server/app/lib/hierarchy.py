"""Orchestration for the hierarchy endpoints: one read, then one pass to nest it."""
from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.repositories.hierarchy import (
    SubtreeRow,
    delete_nodes,
    fetch_ancestor_depths,
    fetch_descendant_ids,
    fetch_parent_id,
    fetch_subtree_rows,
    lock_hierarchy_writes,
    replace_closure,
    upsert_nodes,
)
from app.lib.types.node import HierarchyNode, NodeType


class InvalidHierarchy(Exception):
    """A payload that cannot be stored, as opposed to one that failed to store.

    Raised for the two things the schema cannot express: an id repeated inside one payload,
    and a payload that would make a node its own ancestor. The route turns it into a status
    code; this layer only reports what is wrong, in the same way `get_hierarchy` reports a
    missing node by returning None.
    """


class PayloadNode(NamedTuple):
    """One node of a posted hierarchy, flattened out of the nesting.

    `depth` is relative to the posted root, which is at 0 -- not an absolute depth in the
    stored tree, which the payload does not know and does not need to.
    """

    id: int
    type: NodeType
    parent_id: int | None
    depth: int


class ClosureRow(NamedTuple):
    """`ancestor_id` is `depth` edges above `descendant_id`."""

    ancestor_id: int
    descendant_id: int
    depth: int


def assemble_tree(rows: Sequence[SubtreeRow]) -> HierarchyNode | None:
    """Nest a flat, `(depth, id)`-ordered subtree read into a single root node.

    Iterative on purpose. The obvious version recurses once per level, which would put the
    depth of the stored hierarchy against Python's recursion limit and run that whole call
    chain on the event loop without an await in it. One forward pass has neither problem:
    the ordering means every row's parent was built by an earlier iteration, so attaching a
    node is a dict lookup.

    Returns None when the read came back empty, which is how a request for a node that does
    not exist is told apart from one for a leaf: a stored node always has its own depth-0
    closure row, so "no rows" can only mean "no such node".
    """
    built: dict[int, HierarchyNode] = {}
    root: HierarchyNode | None = None

    for row in rows:
        node = HierarchyNode(id=row.id, type=row.type, children=[])
        if root is None:
            # Depth 0 sorts first, and only the root sits at depth 0 relative to itself.
            # Its own parent_id points outside the subtree (or is NULL) and is ignored:
            # it names where this subtree hangs, which is not what is being returned.
            root = node
        else:
            # Appending mutates the list Pydantic built during validation rather than
            # assigning a new one, so no re-validation runs per child.
            built[row.parent_id].children.append(node)
        built[row.id] = node

    return root


async def get_hierarchy(session: AsyncSession, node_id: int) -> HierarchyNode | None:
    """The node with every descendant nested underneath, or None if there is no such node."""
    return assemble_tree(await fetch_subtree_rows(session, node_id))


def flatten_payload(payload: HierarchyNode) -> list[PayloadNode]:
    """Unnest a posted hierarchy into rows, parents before children.

    Breadth-first with each level sorted by id, which is the same `(depth, id)` order the
    read path returns and the fixtures store their children in. Two things depend on it:
    the closure pass below can look up any node's parent because that parent was handled in
    an earlier iteration, and the upsert's VALUES list names parents before the rows that
    reference them, so it would stay correct even if it ever had to be chunked.

    Iterative rather than recursive, for the reason `assemble_tree` is: a payload deeper
    than Python's recursion limit should store, not raise.

    The posted root comes back with `parent_id=None`. The payload does not name a parent for
    its own root, and None here means "not stated" rather than "no parent" -- the caller
    substitutes whatever the root is currently hanging from.

    Raises InvalidHierarchy if an id appears more than once, which is the one structural
    error the Pydantic model cannot catch: each branch is individually well-formed, so
    nothing is wrong until the branches are looked at together.
    """
    nodes: list[PayloadNode] = []
    seen: set[int] = set()
    level = [(payload, None)]
    depth = 0

    while level:
        level.sort(key=lambda pair: pair[0].id)
        for node, parent_id in level:
            if node.id in seen:
                raise InvalidHierarchy(f'Node {node.id} appears more than once in the payload')
            seen.add(node.id)
            nodes.append(PayloadNode(node.id, node.type, parent_id, depth))
        level = [(child, node.id) for node, _ in level for child in node.children]
        depth += 1

    return nodes


def closure_rows(
    nodes: Sequence[PayloadNode],
    captured_ancestors: Sequence[tuple[int, int]],
) -> list[ClosureRow]:
    """Every ancestor-descendant pair the stored payload implies.

    Three kinds of row per node: its own depth-0 self-row, one for each ancestor inside the
    payload, and one for each ancestor the posted root already had -- those at
    `depth_above_root + depth_within_payload`, since reaching them means walking up to the
    root first. The captured ancestors are what keep a re-posted subtree attached to the
    tree above it instead of silently becoming a new root.

    One forward pass, no recursion and no repeated walking: `nodes` is parent-before-child,
    so a node's ancestors are its parent plus its parent's ancestors, each one deeper.
    """
    rows: list[ClosureRow] = []
    ancestors_within_payload: dict[int, list[tuple[int, int]]] = {}

    for node in nodes:
        if node.parent_id is None:
            within: list[tuple[int, int]] = []
        else:
            within = [
                (node.parent_id, 1),
                *(
                    (ancestor, depth + 1)
                    for ancestor, depth in ancestors_within_payload[node.parent_id]
                ),
            ]
        ancestors_within_payload[node.id] = within

        rows.append(ClosureRow(node.id, node.id, 0))
        rows.extend(ClosureRow(ancestor, node.id, depth) for ancestor, depth in within)
        rows.extend(
            ClosureRow(ancestor, node.id, depth + node.depth)
            for ancestor, depth in captured_ancestors
        )

    return rows


async def store_hierarchy(session: AsyncSession, payload: HierarchyNode) -> HierarchyNode:
    """Store a posted hierarchy as an upsert, and return what was stored.

    The payload is authoritative for the entire subtree under every node it mentions:
    anything currently beneath one of them that the payload does not list is removed. That
    is what makes re-posting a changed hierarchy handle additions, removals and moves in one
    request, including a node migrating in from a different tree.

    Both representations are written together. `nodes.parent_id` holds the shape and
    `node_closure` is derived from it, and no constraint can check that the two agree -- this
    function is the only thing that keeps them consistent, which is why the tests assert the
    agreement directly rather than trusting the steps below.
    """
    nodes = flatten_payload(payload)
    payload_ids = {node.id for node in nodes}

    # Before any read, so that everything below sees one unchanging stored state.
    await lock_hierarchy_writes(session)

    captured_ancestors = [
        (row.ancestor_id, row.depth) for row in await fetch_ancestor_depths(session, payload.id)
    ]
    already_above = payload_ids & {ancestor for ancestor, _ in captured_ancestors}
    if already_above:
        raise InvalidHierarchy(
            f'Node {min(already_above)} is already an ancestor of {payload.id}, '
            'so storing it underneath would form a cycle'
        )
    # That one check is the whole cycle test. Every other payload node takes its parent from
    # inside the payload, which is a tree and so acyclic; the single edge leaving the payload
    # is the root's, and the chain above the root cannot pass back through the payload
    # without some payload node being an ancestor of the root -- which is what was just
    # rejected.

    root_parent_id = await fetch_parent_id(session, payload.id)
    doomed = await fetch_descendant_ids(session, payload_ids) - payload_ids

    # Upsert first, delete second. `nodes.parent_id` refuses a delete that would orphan a
    # child, so a node this request moves out of a subtree that is going away still points at
    # its old parent until this statement rewrites it -- delete first and Postgres rejects
    # the statement. Re-parenting first clears the hazard.
    #
    # The delete below can then name a whole subtree at once because the constraint is not
    # deferrable and so is checked when the statement finishes rather than row by row. That
    # is true of RESTRICT and NO ACTION alike; the choice between them is about something
    # else, and neither buys or costs the ordering above.
    await upsert_nodes(
        session,
        [
            {
                'id': node.id,
                'type': node.type,
                # The root keeps where it already hangs. Writing the None that
                # `flatten_payload` reports would detach it from the tree above it.
                'parent_id': root_parent_id if node.depth == 0 else node.parent_id,
            }
            for node in nodes
        ],
    )

    if doomed:
        await delete_nodes(session, doomed)

    await replace_closure(
        session,
        payload_ids,
        [row._asdict() for row in closure_rows(nodes, captured_ancestors)],
    )

    # Read back inside the same transaction: it sees its own writes, it is still covered by
    # the advisory lock, and it hands the caller the canonical stored shape -- normalised
    # child ordering included -- rather than an echo of what they sent.
    stored = await get_hierarchy(session, payload.id)
    await session.commit()

    # Not Optional in practice: the root was upserted above and given a depth-0 closure row,
    # so the read behind get_hierarchy has at least that row to find.
    return stored
