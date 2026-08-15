"""The flat row shapes the storage layer speaks.

These are what crosses the `HierarchyRepository` boundary, in both directions: what a read
hands back and what a write is given. Naming them in `app/lib/repositories/base.py` is what
keeps that interface free of SQLAlchemy -- a repository that returned a driver's row type
would be a Postgres interface with `@abstractmethod` written on it, and a second
implementation would have to fabricate rows belonging to a library it does not use.

The nested `HierarchyNode` in app/lib/types/node.py is the shape the API and the use cases
speak; these are the flat shape the tables hold. Converting between the two is the whole job
of app/lib/hierarchy.py.

`NamedTuple` rather than Pydantic, because nothing here is ever built from untrusted input:
these are read out of the database or computed from an already-validated payload. What the
names buy is attribute access at the call sites, `_asdict()` where an implementation wants
mappings, and hashability -- the write tests compare whole sets of closure rows.
"""
from typing import NamedTuple

from app.lib.types.node import NodeType


class SubtreeRow(NamedTuple):
    """One node of a subtree read, with how far below the requested root it sits.

    `depth` is relative to the root that was asked for, not an absolute depth in the stored
    tree. `parent_id` is the node's real parent, so on the root row it names a node outside
    the subtree being returned -- or nothing, if the root is a root.
    """

    id: int
    type: NodeType
    parent_id: int | None
    depth: int


class AncestorRow(NamedTuple):
    """`ancestor_id` is `depth` edges above the node the read asked about."""

    ancestor_id: int
    depth: int


class NodeRow(NamedTuple):
    """One row of `nodes` as a write states it: the node, and where it hangs."""

    id: int
    type: NodeType
    parent_id: int | None


class ClosureRow(NamedTuple):
    """`ancestor_id` is `depth` edges above `descendant_id`."""

    ancestor_id: int
    descendant_id: int
    depth: int
