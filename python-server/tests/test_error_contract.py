"""Covers the error contract: one body shape, mapped once, never leaking the database.

The point of these tests is not that each endpoint answers with the right number -- the
endpoint tests already assert that. It is that the *mapping* lives on the app rather than in
the routes, so a route that does not exist yet inherits it, and that every refusal a caller
can provoke comes back looking the same.
"""
import importlib

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# Anything that would give away the storage layer. A body carrying any of these has handed
# the caller either a statement or a piece of the schema.
LEAKED_INTERNALS = ['select', 'insert', 'update', 'delete', 'node_closure', 'nodes.', 'psycopg']


def tree(node_id, node_type, children):
    return {'id': node_id, 'type': node_type, 'children': children}


def nested_payload_json(depth):
    """A chain `depth` nodes long, rendered straight to JSON text.

    Built as a string rather than as dicts run through `json.dumps`, because the encoder
    recurses per level and would hit Python's recursion limit inside the test before the
    request was ever sent -- which is the client-side echo of the server-side problem this
    is here to check.
    """
    body = f'{{"id":{depth},"type":"resource_group","children":[]}}'
    for node_id in range(depth - 1, 0, -1):
        body = f'{{"id":{node_id},"type":"management_group","children":[{body}]}}'
    return body


def assert_shared_error_shape(response):
    """The one thing every error body promises: a `detail` key, holding a string, alone."""
    body = response.json()
    assert set(body) == {'detail'}, body
    assert isinstance(body['detail'], str), body
    assert body['detail'], 'an error with no explanation in it'


def assert_names_no_internals(response):
    body = response.text.lower()
    for internal in LEAKED_INTERNALS:
        assert internal not in body, f'error body leaked {internal!r}: {response.text}'


async def stored_node_ids(engine):
    async with engine.connect() as connection:
        result = await connection.execute(text('SELECT id FROM nodes'))
        return {row.id for row in result}


async def a_404(client):
    return await client.get('/hierarchy/999999')


async def a_409(client):
    """A repeated id -- the one structural error the schema cannot express."""
    return await client.post(
        '/hierarchy',
        json=tree(1, 'management_group', [tree(2, 'subscription', []), tree(2, 'subscription', [])]),
    )


async def a_422(client):
    return await client.post('/hierarchy', json=tree(1, 'no-such-type', []))


async def test_every_status_uses_the_same_body(database_engine, client):
    """The acceptance criterion, in one place: 404, 409 and 422 are parsed identically.

    Asserted together rather than one per test, because the claim is about the three of
    them agreeing -- a per-status assertion would still pass while they drifted apart.
    """
    responses = {
        404: await a_404(client),
        409: await a_409(client),
        422: await a_422(client),
    }

    for expected_status, response in responses.items():
        assert response.status_code == expected_status, response.text
        assert_shared_error_shape(response)
        assert_names_no_internals(response)


async def test_a_validation_error_says_where_it_failed(database_engine, client):
    """Normalising Pydantic's list of error objects into one string keeps the location.

    The stock 422 body is `{"detail": [{"loc": [...], "msg": ...}, ...]}`, which is a
    different shape from every other error the API can return. Flattening it is only worth
    doing if the client can still tell which field was wrong.
    """
    response = await a_422(client)

    detail = response.json()['detail']
    assert 'body.type' in detail, detail
    assert 'management_group' in detail, detail


async def test_a_long_list_of_validation_errors_is_truncated(database_engine, client):
    """A malformed payload can fail once per node. The response says how many it left out
    rather than growing with the request that caused it."""
    broken = [{'id': index, 'type': 'no-such-type'} for index in range(2, 6)]

    response = await client.post('/hierarchy', json=tree(1, 'management_group', broken))

    assert response.status_code == 422
    assert_shared_error_shape(response)
    detail = response.json()['detail']
    # Two failures per child -- the bad type and the missing `children` -- so eight in all.
    assert detail.count(';') == 5, detail
    assert 'and 3 more' in detail, detail


async def test_the_handler_is_registered_on_the_app_not_the_route(monkeypatch):
    """What moving the mapping out of the route actually buys, asserted directly.

    A route written later, in a module that knows nothing about status codes, raises
    `InvalidHierarchy` and gets the same 409 in the same shape -- with no `try/except` of
    its own, and without this test touching the hierarchy endpoints at all.
    """
    monkeypatch.setenv('DATABASE_URL', 'postgresql://aryon:aryon@postgres:5432/aryondb')
    controllers = importlib.import_module('app.lib.hierarchy')
    app = importlib.import_module('app.api.main').create_app()

    @app.get('/a-route-invented-by-this-test')
    async def raise_invalid_hierarchy() -> None:
        raise controllers.InvalidHierarchy('Node 7 is already an ancestor of 3')

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/a-route-invented-by-this-test')

    assert response.status_code == 409
    assert response.json() == {'detail': 'Node 7 is already an ancestor of 3'}


async def test_an_unmapped_failure_still_answers_in_the_same_shape(monkeypatch):
    """The contract is only worth having if it holds for the case nobody planned for.

    Starlette answers an unhandled exception with the bare string `Internal Server Error`,
    which is the one body a client parsing `detail` cannot read. `raise_app_exceptions` is
    off because Starlette re-raises after sending the response, so the traceback still
    reaches the server log -- the point being tested is what the caller gets, not that the
    failure was swallowed. It is not.
    """
    monkeypatch.setenv('DATABASE_URL', 'postgresql://aryon:aryon@postgres:5432/aryondb')
    app = importlib.import_module('app.api.main').create_app()

    @app.get('/a-route-that-breaks')
    async def break_in_a_way_nobody_mapped() -> None:
        raise RuntimeError('postgresql://aryon:aryon@postgres:5432/aryondb refused SELECT 1')

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get('/a-route-that-breaks')

    assert response.status_code == 500
    assert_shared_error_shape(response)
    assert_names_no_internals(response)
    assert 'aryon' not in response.text, 'an unhandled exception leaked its message'


async def post_giving_node_2_a_second_parent(client, monkeypatch):
    """Post a payload whose closure violates `uq_node_single_parent`.

    That index permits one depth-1 closure row per node, which is what makes the stored
    graph a forest. The write path is supposed to guarantee it without help, so provoking
    the index means simulating a bug in that path: the closure computation is replaced with
    one that emits a second depth-1 row for node 2, naming node 3 as its parent alongside
    node 1. Node 3 is in the payload and so exists by the time the row is inserted -- the
    row breaks no foreign key and no other rule, which leaves the partial unique index as
    the only thing that can refuse it.
    """
    controllers = importlib.import_module('app.lib.hierarchy')
    correct_closure_rows = controllers.closure_rows

    def with_a_second_parent_for_node_2(nodes, captured_ancestors):
        return [
            *correct_closure_rows(nodes, captured_ancestors),
            controllers.ClosureRow(ancestor_id=3, descendant_id=2, depth=1),
        ]

    monkeypatch.setattr(controllers, 'closure_rows', with_a_second_parent_for_node_2)

    return await client.post(
        '/hierarchy',
        json=tree(
            1, 'management_group', [tree(2, 'subscription', []), tree(3, 'subscription', [])]
        ),
    )


async def test_a_constraint_violation_is_a_409(database_engine, client, monkeypatch):
    """The database refusing a write is a refused request, not a stack trace."""
    response = await post_giving_node_2_a_second_parent(client, monkeypatch)

    assert response.status_code == 409, response.text
    assert_shared_error_shape(response)
    # The same 409 a duplicate id gets, but by a different route -- asserting the wording
    # is what says the database refused this one, rather than the controller having caught
    # it first and made the index irrelevant to the test.
    errors = importlib.import_module('app.api.errors')
    assert response.json() == {'detail': errors.INTEGRITY_DETAIL}
    assert await stored_node_ids(database_engine) == set(), 'a refused write left rows behind'


async def test_a_constraint_violation_names_no_sql(database_engine, client, monkeypatch):
    """`str(IntegrityError)` carries the failing statement and its parameters. None of it
    reaches the caller -- which is the whole reason the handler writes a fixed message."""
    response = await post_giving_node_2_a_second_parent(client, monkeypatch)

    assert_names_no_internals(response)
    assert 'uq_node_single_parent' not in response.text


async def test_a_rejected_path_parameter_reads_like_a_rejected_body(database_engine, client):
    """FastAPI validates a path parameter and a request body by different routes, and both
    end at the same handler. That the two agree is what the contract is for -- the status
    codes themselves are already covered where the endpoints are tested."""
    response = await client.get(f'/hierarchy/{2**63}')

    assert response.status_code == 422, response.text
    assert_shared_error_shape(response)
    assert_names_no_internals(response)
    assert 'path.node_id' in response.json()['detail'], response.text


async def test_a_payload_too_deep_to_validate_is_refused_rather_than_a_500(
    database_engine, client
):
    """The acceptance criterion at its least comfortable: no 500 for *any* reachable input.

    Nested past Pydantic's recursion guard, the payload is rejected -- and FastAPI's stock
    422 handler then dies serialising the rejection, because the error it reports carries
    the offending value, which is the whole nested payload. A refused request became a
    stack trace. The handler in app/api/errors.py reads the location and the message and never
    touches the input, so the refusal stays a refusal.

    The exact code is left open on purpose: how deep a payload gets before something gives
    up depends on how much stack the interpreter had left, so the guard can fire in the
    JSON parser (400) or in validation (422). Which of the two is not the claim being made
    here -- that it is a 4xx with a readable body, and that nothing was stored, is.
    """
    response = await client.post(
        '/hierarchy',
        content=nested_payload_json(1000),
        headers={'content-type': 'application/json'},
    )

    assert 400 <= response.status_code < 500, response.status_code
    assert_shared_error_shape(response)
    assert await stored_node_ids(database_engine) == set()
