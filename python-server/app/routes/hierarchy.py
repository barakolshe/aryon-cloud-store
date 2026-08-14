"""HTTP surface for the hierarchy endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.hierarchy import get_hierarchy, store_hierarchy
from app.database import get_session
from app.schemas.node import HierarchyNode, NodeId

router = APIRouter()

# Annotated rather than `session: AsyncSession = Depends(get_session)`: same dependency,
# no default value on the parameter.
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.get('/hierarchy/{node_id}', response_model=HierarchyNode)
async def read_hierarchy(node_id: NodeId, session: SessionDependency) -> HierarchyNode:
    """Return the node and everything nested underneath it.

    `NodeId` rather than a bare `int` is what makes a non-numeric or unstorably large id a
    422 from FastAPI rather than a database error, and an empty read a 404 rather than an
    empty object. The 404 is raised here because this is the layer that speaks HTTP -- the
    controller reports "no such node" by returning None and stays unaware of response
    codes. Everything else this endpoint can answer with is mapped in app/errors.py.
    """
    hierarchy = await get_hierarchy(session, node_id)
    if hierarchy is None:
        raise HTTPException(status_code=404, detail=f'No node with id {node_id}')
    return hierarchy


@router.post('/hierarchy', response_model=HierarchyNode)
async def write_hierarchy(payload: HierarchyNode, session: SessionDependency) -> HierarchyNode:
    """Store a hierarchy, replacing whatever is currently beneath the nodes it names.

    Answers 200 with the stored subtree rather than an empty body: it costs one indexed read,
    it proves the write landed, and it hands back the canonical child ordering instead of
    echoing the request. `tests/run_tests.py` ignores the body, so this is additive.

    No `try/except`. `InvalidHierarchy` from the controller and `IntegrityError` from the
    driver both become 409s, decided in app/errors.py -- so the mapping is stated once for
    the app instead of once per route that can raise them.
    """
    return await store_hierarchy(session, payload)
