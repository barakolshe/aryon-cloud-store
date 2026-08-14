"""One row per node in the cloud hierarchy.

`parent_id` is where the shape of the hierarchy lives: every node names its parent, and a
root names nobody. The closure table in app/models/node_closure.py is a derived index over
this column -- it records no fact that `parent_id` does not already state, and could be
rebuilt from it at any time. It exists because reading a whole subtree out of `parent_id`
alone means a recursive CTE, which Postgres plans badly.

Keeping the parent here rather than reading it back out of the closure's depth-1 rows is
what holds the read path to a single join: the row already being fetched for `type` carries
the parent with it. Both representations are written in the same transaction, and the write
path is what keeps them agreeing -- no constraint can check that for us.
"""
from sqlalchemy import BigInteger, Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.schemas.node import NodeType


class Node(Base):
    """A management group, subscription, or resource group."""

    __tablename__ = 'nodes'

    # autoincrement=False because ids are supplied by the client and are never generated
    # here. Left at the default, SQLAlchemy reads a BigInteger primary key as
    # auto-incrementing and compiles it to BIGSERIAL, leaving Postgres owning a sequence
    # that every insert overrides and that would drift out of step with the stored ids.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    # The same three values the API speaks, so the database rejects a fourth rather than
    # storing it. values_callable is load-bearing: without it SQLAlchemy persists enum
    # member *names*, and the Postgres labels would come out as 'MANAGEMENT_GROUP'
    # instead of the 'management_group' the payloads use.
    type: Mapped[NodeType] = mapped_column(
        Enum(
            NodeType,
            name='node_type',
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    # NULL for a root, which is why this is the one nullable column here. A column holds one
    # value, so "a node has at most one parent" is structural rather than something the
    # write path has to be trusted with.
    #
    # RESTRICT rather than CASCADE, deliberately. Cascading here is recursive, so one
    # mistyped DELETE would take an arbitrary amount of the hierarchy with it -- and report
    # "DELETE 1" while doing it. RESTRICT costs the write path nothing, because it already
    # works out the full set of nodes to remove from the closure before removing any of
    # them: naming every node of a subtree in one DELETE satisfies RESTRICT, since the check
    # runs when the statement finishes rather than row by row. Only a partial delete, which
    # would orphan the rows it leaves behind, is refused.
    #
    # It does impose an order: a node the request moves out of a subtree that is going away
    # has to be re-parented before the delete, not after. Under CASCADE that node was
    # silently deleted and re-inserted, which holds only while every column here is restated
    # by the request -- the first column that is not would be lost on every move.
    #
    # End-of-statement checking also means an INSERT may name a parent that appears further
    # down the same statement, so a payload goes in as one INSERT with no topological sort.
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey('nodes.id', ondelete='RESTRICT', name='fk_nodes_parent'),
        nullable=True,
    )

    __table_args__ = (
        # Postgres does not index the referencing side of a foreign key on its own, and the
        # constraint above has to look for a node's children on every delete. Unindexed,
        # that is a sequential scan of this table per deleted row.
        Index('ix_nodes_parent_id', 'parent_id'),
    )
