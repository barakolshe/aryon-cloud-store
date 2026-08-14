"""The assignment's sample hierarchies, and the two flat forms the database stores them in.

`tests/objects/*.json` at the repository root is the contract this server is measured against,
so the tests read those files rather than restating their shapes. Nothing here imports `app`:
these helpers describe what the database should contain, independently of the code that puts
it there.
"""
import json
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
