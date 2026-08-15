"""HTTP surface for the hierarchy endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.database import get_session
from app.lib.hierarchy import HierarchyService
from app.lib.repositories.postgres_hierarchy import PostgresHierarchyRepository
from app.lib.types.node import NODE_ID_MAX, NODE_ID_MIN, HierarchyNode

router = APIRouter()

# Annotated rather than `session: AsyncSession = Depends(get_session)`: same dependency,
# no default value on the parameter.
SessionDependency = Annotated[AsyncSession, Depends(get_session)]

# The same BIGINT bounds the request body gets, applied to the path. `Path` rather than the
# `NodeId` alias from app/lib/types/node.py: FastAPI wants a parameter class here, and
# reusing the bare `Field` meant for a model attribute is ambiguous. Without it an id past
# 2**63-1 parses fine as a Python int and fails in psycopg instead, which is a 500 for a
# malformed request.
NodeIdPath = Annotated[int, Path(ge=NODE_ID_MIN, le=NODE_ID_MAX)]


def build_service(session: SessionDependency) -> HierarchyService:
    """Choose the implementation the use cases run against.

    This is the composition root, and it belongs up here rather than in `app/lib/`: naming
    `PostgresHierarchyRepository` is the one decision the reusable layer deliberately does
    not make, which is what leaves it reusable. Wiring it per router follows app/api/main.py
    -- each router carries its own, so adding an endpoint means adding a module.

    Depending on `get_session` rather than building a session keeps the test override
    working: conftest.py replaces that dependency, and FastAPI resolves it through this one.
    """
    return HierarchyService(PostgresHierarchyRepository(session))


ServiceDependency = Annotated[HierarchyService, Depends(build_service)]


@router.get('/hierarchy/{node_id}', response_model=HierarchyNode)
async def read_hierarchy(node_id: NodeIdPath, service: ServiceDependency) -> HierarchyNode:
    """Return the node and everything nested underneath it.

    The typed path parameter is what makes a non-numeric or out-of-range id a 422 from
    FastAPI rather than a database error, and an empty read a 404 rather than an empty
    object. The 404 is raised here because this is the layer that speaks HTTP -- the service
    reports "no such node" by returning None and stays unaware of response codes. Everything
    else this endpoint can answer with is mapped in app/api/errors.py.
    """
    hierarchy = await service.get_hierarchy(node_id)
    if hierarchy is None:
        raise HTTPException(status_code=404, detail=f'No node with id {node_id}')
    return hierarchy


@router.post('/hierarchy', response_model=HierarchyNode)
async def write_hierarchy(payload: HierarchyNode, service: ServiceDependency) -> HierarchyNode:
    """Store a hierarchy, replacing whatever is currently beneath the nodes it names.

    Answers 200 with the stored subtree rather than an empty body: it costs one indexed read,
    it proves the write landed, and it hands back the canonical child ordering instead of
    echoing the request. `tests/run_tests.py` ignores the body, so this is additive.

    No `try/except`. `InvalidHierarchy` from the service and `IntegrityError` from the driver
    both become 409s, decided in app/api/errors.py -- so the mapping is stated once for the
    app instead of once per route that can raise them.
    """
    return await service.store_hierarchy(payload)
