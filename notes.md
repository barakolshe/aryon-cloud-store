# Notes

Design notes and known trade-offs — things the implementation leaves on the table on purpose.

## Pagination

To make it more efficient I would add pagination to `GET /hierarchy/{node_id}`.

Today the endpoint returns the entire subtree in a single response. The query cost is fine — one
range scan on the closure's primary key, one join, no recursive CTE — but the *response* is
unbounded: a management group with 100k descendants is still one JSON body, assembled in memory on
the server and parsed in one piece by the client.

The read already sorts by `(depth, id)`, which is exactly the shape a keyset cursor wants, so
pagination would not need a new index or a different query:

- **Keyset cursor** — `limit` plus an `after=(depth, id)` cursor, walking the subtree level by level
  and letting the client stop early. `WHERE (c.depth, n.id) > (:depth, :id)` continues from the
  cursor without an `OFFSET` scan.
- **Depth cap** — `?max_depth=n` adding `AND c.depth <= :n`, for callers that only want the top of
  the tree. Cheap, and it is the common case for rendering a collapsible tree view one level at a
  time.

Both change the response contract (the body would carry a cursor alongside the nodes, and a partial
tree is no longer byte-identical to what was stored), so neither is built: the assignment's fixtures
post a hierarchy and fetch the whole thing back, and `tests/run_tests.py` compares the two directly.
