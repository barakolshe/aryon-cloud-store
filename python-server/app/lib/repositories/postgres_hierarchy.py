"""The Postgres implementation of `HierarchyRepository` -- the SQL behind the endpoints.

Named for its database rather than for the thing it stores, because what is in here is
genuinely Postgres and not merely SQL: an advisory lock, `ON CONFLICT DO UPDATE`, and a
reliance on non-deferrable foreign keys being checked at the end of a statement rather than
row by row. A second implementation would not port these decisions; it would make its own.

Everything crossing back out is a plain `NamedTuple` from app/lib/types/rows.py. Converting
costs one allocation per row on the read path -- against the Pydantic model `assemble_tree`
builds for each of those same rows, that is noise, and it is what keeps a SQLAlchemy `Row`
from becoming part of the interface every caller is written against.
"""
from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.models.node import Node
from app.lib.models.node_closure import NodeClosure
from app.lib.repositories.base import HierarchyRepository
from app.lib.types.rows import AncestorRow, ClosureRow, NodeRow, SubtreeRow

# The key every hierarchy write locks on. The value is arbitrary; what matters is that it
# is a constant, so all writers queue behind the same lock.
#
# One lock for the whole hierarchy serialises writes to disjoint trees as well as to
# overlapping ones. That is a deliberate simplicity-for-throughput trade, not an oversight:
# a write reads the stored shape, decides what to remove from it, and then rewrites both
# representations, so two concurrent writes that touch any common node can interleave into a
# closure that matches neither payload. Should throughput ever matter more than the
# simplicity, the upgrade is SERIALIZABLE plus a retry wrapper on serialisation failures,
# not a finer-grained lock -- row-level `FOR UPDATE` on the payload ids cannot work here,
# because the set a write affects includes rows that do not exist yet, which makes it
# phantom-prone by construction.
HIERARCHY_WRITE_LOCK = 6_040_982_311


class PostgresHierarchyRepository(HierarchyRepository):
    """One request's worth of hierarchy storage, backed by one SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        # Held rather than passed per call, which is the point of the class: with the
        # session in here, nothing above this layer has to import SQLAlchemy to reach the
        # database. The session's lifetime is the request's -- app/lib/database.py opens it
        # and closes it -- so this object is scoped to one unit of work, as the base class
        # says an implementation may assume.
        self.session = session

    async def fetch_subtree_rows(self, root_id: int) -> Sequence[SubtreeRow]:
        """Every node at or below `root_id`, parents before children.

        One statement: a range scan on the closure's primary key for `ancestor_id`, joined
        to `nodes` for the type and the parent. No recursive CTE, and no second query for
        the root -- the closure's depth-0 self-row puts it in the same result set.

        The ordering is load-bearing rather than cosmetic. `(depth, id)` guarantees a node
        is returned before any of its children, so the caller can attach each row to a
        parent it has already built in one forward pass; and it guarantees siblings arrive
        in ascending id order, which is the child order the stored fixtures expect.
        """
        statement = (
            # select_from is not optional. Left to itself SQLAlchemy takes the leftmost FROM
            # from the first column in the select list -- Node -- and the join below would
            # then be nodes joined to nodes. Naming the closure as the driving table is what
            # makes this the intended plan.
            select(Node.id, Node.type, Node.parent_id, NodeClosure.depth)
            .select_from(NodeClosure)
            .join(Node, Node.id == NodeClosure.descendant_id)
            .where(NodeClosure.ancestor_id == root_id)
            .order_by(NodeClosure.depth, Node.id)
        )
        result = await self.session.execute(statement)
        return [SubtreeRow(*row) for row in result]

    async def lock_writes(self) -> None:
        """Queue behind every other hierarchy write until this transaction ends.

        `pg_advisory_xact_lock` rather than `pg_advisory_lock`: the transaction-scoped form
        is released by COMMIT or ROLLBACK, so a request that fails midway cannot leave the
        lock held. See HIERARCHY_WRITE_LOCK for why the lock is this coarse.
        """
        await self.session.execute(select(func.pg_advisory_xact_lock(HIERARCHY_WRITE_LOCK)))

    async def fetch_ancestor_depths(self, node_id: int) -> Sequence[AncestorRow]:
        """Everything currently above `node_id`, with its distance.

        Taken before the write mutates anything, for two purposes: it is the cycle check
        (a payload may not contain a node that is already above its own root), and it is
        what lets the rebuilt closure re-attach the payload to the tree the root hangs from.

        `depth > 0` drops the node's own self-row -- it is not above itself. Served by
        `ix_closure_descendant_depth`, which exists for this lookup.
        """
        statement = select(NodeClosure.ancestor_id, NodeClosure.depth).where(
            NodeClosure.descendant_id == node_id,
            NodeClosure.depth > 0,
        )
        result = await self.session.execute(statement)
        return [AncestorRow(*row) for row in result]

    async def fetch_parent_id(self, node_id: int) -> int | None:
        """Where `node_id` currently hangs, or None if it is a root or is not stored yet.

        Read from `nodes` rather than from the closure's depth-1 row on purpose.
        `parent_id` is where the shape is actually stored and the closure is derived from
        it, so reading the derived copy in order to rewrite the authoritative one would have
        the dependency backwards -- and would quietly launder a disagreement between the two
        into the new state instead of leaving it to be caught.
        """
        statement = select(Node.parent_id).where(Node.id == node_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def fetch_descendant_ids(self, node_ids: Iterable[int]) -> set[int]:
        """Every node stored at or below any of `node_ids`, the given nodes included.

        The union over all of them, not just over the posted root. A payload may name a node
        that currently lives somewhere else entirely, and the rule is that the payload is
        authoritative for everything beneath every node it mentions -- so a moved-in node's
        stored children have to be in this set, or they are silently orphaned.
        """
        statement = (
            select(NodeClosure.descendant_id)
            .where(NodeClosure.ancestor_id.in_(node_ids))
            .distinct()
        )
        result = await self.session.execute(statement)
        return set(result.scalars())

    async def upsert_nodes(self, rows: Sequence[NodeRow]) -> None:
        """Insert the payload's nodes, updating type and parent for the ones already stored.

        `.values(rows)` rather than `session.execute(statement, rows)`, and the difference
        is load-bearing. The latter compiles to `cursor.executemany`, which psycopg sends as
        one statement per row -- and a non-deferrable foreign key is checked at the end of
        each statement, so a row naming a parent that arrives later would be rejected.
        Building a single INSERT with every tuple in one VALUES list lets rows reference
        parents further down the same statement, which is what removes the need for a
        topological sort.
        """
        statement = postgresql_insert(Node).values([row._asdict() for row in rows])
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[Node.id],
                set_={
                    'type': statement.excluded.type,
                    'parent_id': statement.excluded.parent_id,
                },
            )
        )

    async def delete_nodes(self, node_ids: Iterable[int]) -> None:
        """Remove nodes the payload dropped. Their closure rows cascade.

        One statement naming the whole set, which is what lets a subtree go at once: the
        foreign key on `nodes.parent_id` is checked when the statement finishes rather than
        row by row, so a parent and its children may be removed together. A *partial*
        delete, one that would leave a child pointing at a node that is gone, is still
        refused -- which is the guarantee worth having.
        """
        await self.session.execute(delete(Node).where(Node.id.in_(node_ids)))

    async def replace_closure(
        self, descendant_ids: Iterable[int], rows: Sequence[ClosureRow]
    ) -> None:
        """Swap the closure rows of the payload's nodes for freshly computed ones.

        Scoped by descendant: a node's rows say where it sits, so moving it invalidates
        exactly the rows naming it as descendant, and nothing about the nodes it passes
        over. Rows naming a payload node as *ancestor* need no separate cleanup -- their
        descendants are either payload nodes, rewritten here, or nodes just deleted, whose
        rows cascaded.
        """
        await self.session.execute(
            delete(NodeClosure).where(NodeClosure.descendant_id.in_(descendant_ids))
        )
        await self.session.execute(insert(NodeClosure).values([row._asdict() for row in rows]))

    async def commit(self) -> None:
        """Commit the transaction, releasing the advisory lock with it.

        The session itself is closed by whoever opened it -- app/lib/database.py's
        per-request dependency -- so anything left uncommitted when a request fails is
        rolled back there rather than here.
        """
        await self.session.commit()
