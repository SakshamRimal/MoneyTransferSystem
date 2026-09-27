"""
Generic repository base class.

Demonstrates the "abstraction" and "generics/inheritance" side of the OOP
pillars used deliberately in this codebase: concrete repositories inherit
common `get_by_id` / `add` behaviour and only implement what's specific to
their aggregate (custom queries, locking strategy, etc). Session lifecycle
(commit/rollback) is intentionally *not* handled here -- that is owned by
the `get_db_session` dependency / service layer's unit-of-work boundary, so
repositories stay simple and composable within a single request's
transaction.
"""

import uuid
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    model: type[ModelType]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelType | None:
        result = await self.session.execute(select(self.model).where(self.model.id == entity_id))
        return result.scalar_one_or_none()

    def add(self, entity: ModelType) -> ModelType:
        self.session.add(entity)
        return entity
