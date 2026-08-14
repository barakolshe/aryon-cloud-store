"""The closure table: one row for every ancestor-descendant pair in the hierarchy.

A parent_id column would make reads a recursive CTE whose cost grows with depth. Storing
the transitive closure instead -- every ancestor of a node, not just its parent, with the
number of edges between them -- turns "fetch this node and everything under it" into a
single indexed lookup on `ancestor_id`. The trade is on writes: moving a subtree
recomputes O(nodes x depth) rows. Reads were chosen as the hot path.

Every node also gets a depth-0 row pointing at itself, written by the POST path rather
than by the migration. It is what makes "this node exists" distinguishable from "this node
has no descendants" -- without it a leaf and a missing id both come back as zero rows, and
GET could not tell a 200 from a 404.
"""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NodeClosure(Base):
    """`ancestor_id` is `depth` edges above `descendant_id`."""

    __tablename__ = 'node_closure'

    # ON DELETE CASCADE both ways: deleting a node takes its closure rows with it, so the
    # write path can delete from `nodes` alone and never leave a row pointing at nothing.
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
        # >= 0 rather than > 0: depth 0 is the self-row every node carries.
        CheckConstraint('depth >= 0', name='ck_node_closure_depth_non_negative'),
        # Covers the read query, which filters on descendant_id and depth to find a
        # node's parent; the primary key already covers lookups by ancestor_id.
        Index('ix_closure_descendant_depth', 'descendant_id', 'depth'),
        # At most one depth-1 row per node means at most one parent, so the stored graph
        # can only ever be a forest. Data integrity the README asks for, enforced by
        # Postgres instead of by application code that a future caller could bypass.
        Index(
            'uq_node_single_parent',
            'descendant_id',
            unique=True,
            postgresql_where=text('depth = 1'),
        ),
    )
