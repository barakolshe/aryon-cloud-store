"""The `tenants` table -- the reference endpoint's data."""
import uuid

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Tenant(Base):
    """Mirrors the table `postgres/init.sql` seeds with microsoft, amazon and google.

    No `server_default` for the generated id: that is DDL, and the database already
    declares it. This class describes the columns a query reads, nothing more.
    """

    __tablename__ = 'tenants'

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tenant_name: Mapped[str] = mapped_column(String(255), unique=True)
