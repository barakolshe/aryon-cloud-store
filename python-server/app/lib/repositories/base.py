"""The storage interface the hierarchy use cases are written against.

Nothing here names SQLAlchemy, a session, or a dialect. The methods take ids and the row
types from app/lib/types/rows.py and hand back the same, which is what lets
`HierarchyService` hold one of these without learning which database is underneath.

The transaction is part of the interface rather than something a caller reaches around it
for. `commit` is here because the use case is what knows when a unit of work is finished --
leaving it off would have meant handing the service a session alongside the repository,
which is exactly the coupling this class exists to remove.

Read the docstrings below as the contract every implementation owes. They say what a caller
may rely on, deliberately without saying how it is achieved;
app/lib/repositories/postgres_hierarchy.py is where the how lives, and where the reasoning
behind each statement is written down.
"""
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from app.lib.types.rows import AncestorRow, ClosureRow, NodeRow, SubtreeRow


class HierarchyRepository(ABC):
    """Everything the hierarchy use cases need a store to do.

    One instance is one unit of work: an implementation is free to hold a connection, a
    transaction, or nothing at all, so callers should treat an instance as scoped to a
    single request rather than shared.
    """

    @abstractmethod
    async def fetch_subtree_rows(self, root_id: int) -> Sequence[SubtreeRow]:
        """Every node at or below `root_id`, ordered by `(depth, id)`.

        Both halves of that order are load-bearing. Ordering by depth is what lets a caller
        attach each row to a parent it has already seen, in one forward pass; the id
        tiebreak is the sibling order the stored fixtures are compared against.

        `root_id` itself is included, at depth 0. A node that is not stored therefore comes
        back as no rows at all -- an implementation that omitted the root would leave
        callers unable to tell a missing node from a childless one.
        """

    @abstractmethod
    async def lock_writes(self) -> None:
        """Queue behind every other hierarchy write until this unit of work ends.

        A write reads the stored shape, decides what to remove from it, and then rewrites
        both representations, so two writes touching any node in common can interleave into
        a state that matches neither payload. How an implementation serialises them is its
        own business; the contract is that a caller which has returned from here is alone
        with the hierarchy until it commits or is rolled back.
        """

    @abstractmethod
    async def fetch_ancestor_depths(self, node_id: int) -> Sequence[AncestorRow]:
        """Everything currently above `node_id`, each with its distance.

        Excludes `node_id` itself: a node is not its own ancestor, whatever bookkeeping an
        implementation keeps.
        """

    @abstractmethod
    async def fetch_parent_id(self, node_id: int) -> int | None:
        """Where `node_id` currently hangs.

        None both for a stored root and for a node that is not stored yet. The caller asks
        this to find out what parent to preserve, and "nothing to preserve" is the same
        answer in both cases.
        """

    @abstractmethod
    async def fetch_descendant_ids(self, node_ids: Iterable[int]) -> set[int]:
        """Every node stored at or below any of `node_ids`, those nodes included.

        The union over all of them, not over one root. A payload may name a node that
        currently lives somewhere else entirely, and that node's stored children have to be
        in this set or a write silently orphans them.
        """

    @abstractmethod
    async def upsert_nodes(self, rows: Sequence[NodeRow]) -> None:
        """Insert these nodes, replacing type and parent for the ones already stored.

        `rows` arrives parent-before-child, but an implementation may not assume it can
        apply the rows one at a time in that order: a row may name a parent that is itself
        later in the same sequence, so the batch has to land as a unit.
        """

    @abstractmethod
    async def delete_nodes(self, node_ids: Iterable[int]) -> None:
        """Remove these nodes, and whatever the implementation derives from them.

        The set is always closed under descendants -- the caller works out the full subtree
        before calling -- so an implementation is entitled to refuse a partial delete that
        would orphan a child rather than quietly cascading into one.
        """

    @abstractmethod
    async def replace_closure(
        self, descendant_ids: Iterable[int], rows: Sequence[ClosureRow]
    ) -> None:
        """Swap every stored row naming one of `descendant_ids` as descendant for `rows`.

        Scoped by descendant because a node's rows record where *it* sits: moving a node
        invalidates exactly the rows naming it, and nothing about the nodes it passes over.
        """

    @abstractmethod
    async def commit(self) -> None:
        """End the unit of work, making everything written since it began durable.

        Also what releases whatever `lock_writes` took, so a caller that never reaches here
        must leave the hierarchy unlocked and unchanged.
        """
