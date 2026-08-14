"""The closure table: one row for every ancestor-descendant pair in the hierarchy.

A derived index over `nodes.parent_id`, which is where the shape is actually stored. This
table holds no fact that walking parent_id would not produce; it exists because walking
parent_id means a recursive CTE, and the cost is not the recursion -- it is that Postgres
always materialises a recursive CTE and sizes it with a hardcoded guess of ten iterations. Measured against a synthetic 100k-node tree, the planner
estimated 201 rows for every subtree asked of it, whether the answer was 2 rows or 34,464,
and touched 86,259 buffers to return the large one. The same read from a closure table
estimated 35,458 against an actual 34,464 and touched 371 buffers: one range scan on
`ancestor_id` in place of one index probe per row returned, and -- the part that matters
once this is joined against anything else -- a row count the planner can actually plan on.

The trade lands on writes: re-parenting rewrites the closure rows of every node in the
moved subtree, up to |subtree| x |ancestors| of them. A move between siblings changes one
ancestor per descendant; a move across trees changes all of them. Reads were chosen as the
hot path.
"""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from app.lib.models.base import Base


class NodeClosure(Base):
    """`ancestor_id` is `depth` edges above `descendant_id`."""

    __tablename__ = 'node_closure'

    # ON DELETE CASCADE both ways, unlike nodes.parent_id, which restricts: these rows are
    # bookkeeping about a node rather than data of their own, so they should follow it out.
    # It lets the write path delete from `nodes` alone and never leave a row pointing at
    # nothing, and it cannot lose anything that was not derived in the first place.
    ancestor_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('nodes.id', ondelete='CASCADE', name='fk_node_closure_ancestor'),
        primary_key=True,
    )
    descendant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('nodes.id', ondelete='CASCADE', name='fk_node_closure_descendant'),
        primary_key=True,
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        # >= 0 rather than > 0 because every node carries a depth-0 row pointing at
        # itself, written by the POST path. That self-row is what lets one query do the
        # whole read: `WHERE ancestor_id = :id` returns the node together with its
        # descendants, so the root needs no separate fetch and no UNION. Answering 404 is
        # a side benefit, not the reason -- `SELECT 1 FROM nodes` would do that alone.
        CheckConstraint('depth >= 0', name='ck_node_closure_depth_non_negative'),
        # Covers the lookups that start from the descendant rather than the ancestor --
        # chiefly the write path's capture of a node's current ancestors (depth > 0), taken
        # before the closure is rewritten. The primary key already covers ancestor_id, and
        # the read path gets a node's parent from nodes.parent_id rather than from here.
        Index('ix_closure_descendant_depth', 'descendant_id', 'depth'),
        # nodes.parent_id is what guarantees a single parent now -- a column holds one
        # value. This index is not that guarantee; it is a check on the derived copy,
        # rejecting a write path that emits two depth-1 rows for the same node. What it
        # cannot catch is the two representations disagreeing about *which* node the parent
        # is: both would be singular, both self-consistent. Nothing short of a trigger
        # catches that, and the write path setting them together is the reason it does not
        # happen.
        Index(
            'uq_node_single_parent',
            'descendant_id',
            unique=True,
            postgresql_where=text('depth = 1'),
        ),
    )
