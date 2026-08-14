"""The SQL behind the hierarchy endpoints."""
from collections.abc import Sequence

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.node import Node
from app.models.node_closure import NodeClosure
from app.schemas.node import NodeType

# What one row of the subtree read carries. Spelled out so callers get attribute names
# rather than a bare tuple, and so the shape is checkable where it is consumed.
SubtreeRow = Row[tuple[int, NodeType, int | None, int]]


async def fetch_subtree_rows(session: AsyncSession, root_id: int) -> Sequence[SubtreeRow]:
    """Every node at or below `root_id`, parents before children.

    One statement: a range scan on the closure's primary key for `ancestor_id`, joined to
    `nodes` for the type and the parent. No recursive CTE, and no second query for the root
    -- the closure's depth-0 self-row puts it in the same result set.

    The ordering is load-bearing rather than cosmetic. `(depth, id)` guarantees a node is
    returned before any of its children, so the caller can attach each row to a parent it
    has already built in one forward pass; and it guarantees siblings arrive in ascending
    id order, which is the child order the stored fixtures expect.
    """
    statement = (
        # select_from is not optional. Left to itself SQLAlchemy takes the leftmost FROM
        # from the first column in the select list -- Node -- and the join below would then
        # be nodes joined to nodes. Naming the closure as the driving table is what makes
        # this the intended plan.
        select(Node.id, Node.type, Node.parent_id, NodeClosure.depth)
        .select_from(NodeClosure)
        .join(Node, Node.id == NodeClosure.descendant_id)
        .where(NodeClosure.ancestor_id == root_id)
        .order_by(NodeClosure.depth, Node.id)
    )
    result = await session.execute(statement)
    return result.all()
