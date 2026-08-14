"""One row per node in the cloud hierarchy.

The table holds only what a node *is*. Where it sits in the hierarchy is recorded
separately, in `app/models/node_closure.py`, so moving a subtree never rewrites the nodes
themselves.
"""
from sqlalchemy import BigInteger, Enum
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
