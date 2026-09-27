import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transfer import LedgerEntry, Transfer
from app.repositories.base import BaseRepository


class TransferRepository(BaseRepository[Transfer]):
    model = Transfer

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    async def get_by_idempotency_key(self, key: str) -> Transfer | None:
        result = await self.session.execute(select(Transfer).where(Transfer.idempotency_key == key))
        return result.scalar_one_or_none()

    def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        self.session.add(entry)
        return entry

    async def list_for_account(
        self, account_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[LedgerEntry], int]:
        """Return a page of ledger entries for an account (most recent
        first) plus the total count matching, for pagination metadata."""
        count_result = await self.session.execute(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.account_id == account_id)
        )
        total = count_result.scalar_one()

        items_result = await self.session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.account_id == account_id)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(items_result.scalars().all())
        return items, total
