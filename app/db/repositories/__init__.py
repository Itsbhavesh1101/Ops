"""Base repository abstractions and session-bound repository contracts."""

from app.db.repositories.base import Repository, SqlAlchemyRepository

__all__ = [
    "Repository",
    "SqlAlchemyRepository",
]
