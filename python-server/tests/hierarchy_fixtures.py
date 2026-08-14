"""The assignment's sample hierarchies, and the two flat forms the database stores them in.

`tests/objects/*.json` at the repository root is the contract this server is measured against,
so the tests read those files rather than restating their shapes. Nothing here imports `app`:
these helpers describe what the database should contain, independently of the code that puts
it there.
"""
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

# python-server/tests/ -> python-server/ -> repository root.
FIXTURE_DIRECTORY = Path(__file__).parents[2] / 'tests' / 'objects'


class SubtreeRow(NamedTuple):
    """One row of the subtree read, standing in for a SQLAlchemy Row."""

    id: int
    type: str
    parent_id: int | None
    depth: int


class ClosureRow(NamedTuple):
    """One `node_closure` row: `ancestor_id` is `depth` edges above `descendant_id`."""

    ancestor_id: int
    descendant_id: int
    depth: int


def load_fixture(name: str) -> dict[str, Any]:
    """Read one of the sample hierarchies, e.g. load_fixture('5')."""
    return json.loads((FIXTURE_DIRECTORY / f'{name}.json').read_text())


def subtree_rows(hierarchy: dict[str, Any], root_parent_id: int | None) -> list[SubtreeRow]:
    """The hierarchy as the repository's query returns it: ordered by `(depth, id)`.

    Level by level rather than recursively, which is also how the ordering falls out: a
    whole depth is emitted, sorted by id, before the next one is walked.
    """
    rows: list[SubtreeRow] = []
    level = [(hierarchy, root_parent_id)]
    depth = 0

    while level:
        level.sort(key=lambda pair: pair[0]['id'])
        rows.extend(
            SubtreeRow(node['id'], node['type'], parent_id, depth) for node, parent_id in level
        )
        level = [(child, node['id']) for node, _ in level for child in node['children']]
        depth += 1

    return rows


def closure_rows(hierarchy: dict[str, Any]) -> list[ClosureRow]:
    """Every ancestor-descendant pair in the hierarchy, including each node's depth-0 self-row."""
    rows: list[ClosureRow] = []
    pending = [(hierarchy, [])]

    while pending:
        node, ancestors = pending.pop()
        chain = [*ancestors, node['id']]
        rows.extend(
            ClosureRow(ancestor, node['id'], depth)
            for depth, ancestor in enumerate(reversed(chain))
        )
        pending.extend((child, chain) for child in node['children'])

    return rows


def closure_from_parent_edges(parents: Mapping[int, int | None]) -> set[ClosureRow]:
    """The closure a `{node_id: parent_id}` map implies -- every row, and no others.

    The independent oracle for the write path's post-condition. `nodes.parent_id` is where
    the shape is stored and `node_closure` is derived from it, but nothing in the schema can
    check that the two agree; comparing the stored closure against this rebuild is what
    turns "the write path keeps them consistent" into something a test can fail on.

    Deliberately re-derived from the parent column alone rather than from the payload, so a
    write path that got both representations wrong in the same way still gets caught by the
    tests that compare a fetch against its source file.
    """
    rows: set[ClosureRow] = set()

    for node_id in parents:
        rows.add(ClosureRow(node_id, node_id, 0))
        ancestor = parents[node_id]
        depth = 1
        while ancestor is not None:
            rows.add(ClosureRow(ancestor, node_id, depth))
            ancestor = parents[ancestor]
            depth += 1

    return rows
