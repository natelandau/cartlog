"""SQLAlchemy declarative base shared by all cartlog ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all cartlog ORM models."""
