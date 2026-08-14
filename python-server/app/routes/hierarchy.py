"""HTTP surface for the hierarchy endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.hierarchy import get_hierarchy
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
