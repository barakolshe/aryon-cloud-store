"""HTTP surface for the hierarchy endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.hierarchy import InvalidHierarchy, get_hierarchy, store_hierarchy
from app.database import get_session
from app.schemas.node import HierarchyNode

router = APIRouter()

# Annotated rather than `session: AsyncSession = Depends(get_session)`: same dependency,
# no default value on the parameter.
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.get('/hierarchy/{node_id}', response_model=HierarchyNode)
async def read_hierarchy(node_id: int, session: SessionDependency) -> HierarchyNode:
    """Return the node and everything nested underneath it.

    `node_id: int` is what makes a non-numeric id a 422 from FastAPI rather than a database
    error, and an empty read a 404 rather than an empty object. The status code is decided
    here because this is the layer that speaks HTTP -- the controller reports "no such node"
    by returning None and stays unaware of response codes.
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

    409 for a payload that cannot be stored -- a repeated id, or one that would make a node
    its own ancestor. Decided here for the same reason the 404 above is: the controller
    reports what is wrong and stays unaware of status codes.
    """
    try:
        return await store_hierarchy(session, payload)
    except InvalidHierarchy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
