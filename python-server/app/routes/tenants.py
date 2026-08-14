"""HTTP surface for tenants: the path, the response model, and the dependency wiring."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.tenant import TenantController
from app.database import get_session
from app.repositories.tenant import TenantRepository
from app.schemas.tenant import TenantResponse

router = APIRouter()

# `Annotated[..., Depends(...)]` rather than a default argument: the parameter stays
# required, so a caller that bypasses FastAPI has to supply it instead of silently
# picking up a stand-in.
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_tenant_controller(session: SessionDep) -> TenantController:
    """Assemble the layer chain for one request: session -> repository -> controller.

    The wiring lives in the route module because composition is an HTTP-layer concern --
    it is also the seam tests override to run the endpoint without a database.
    """
    return TenantController(TenantRepository(session))


ControllerDep = Annotated[TenantController, Depends(get_tenant_controller)]


@router.get('/tenants', response_model=list[TenantResponse])
async def list_tenants(controller: ControllerDep) -> list[TenantResponse]:
    """Every stored tenant."""
    return await controller.list_tenants()
