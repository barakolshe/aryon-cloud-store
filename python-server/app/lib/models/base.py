"""The declarative base every SQLAlchemy model inherits."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Collects the table definitions of every model that subclasses it.

    `Base.metadata` is the single description of the schema, which is what Alembic
    compares the live database against -- so a model only counts once it is imported.
    """
