"""The response contract for GET /tenants."""
import uuid

from pydantic import BaseModel, ConfigDict


class TenantResponse(BaseModel):
    """One row of GET /tenants.

    Named for the direction it travels rather than the table, so it never reads
    ambiguously beside the ORM `Tenant` it is built from.

    `from_attributes` lets the controller validate a SQLAlchemy row straight into this
    model. `tenant_id` serialises as the plain UUID string -- the same text the raw-SQL
    version emitted before the layers were split apart, so the body is unchanged.
    """

    model_config = ConfigDict(from_attributes=True)

    tenant_id: uuid.UUID
    tenant_name: str
