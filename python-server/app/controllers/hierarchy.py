"""Orchestration for the hierarchy endpoints: one read, then one pass to nest it."""
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.hierarchy import SubtreeRow, fetch_subtree_rows
from app.schemas.node import HierarchyNode


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
