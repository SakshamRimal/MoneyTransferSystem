import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    model = Account

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    async def get_for_update(self, account_id: uuid.UUID) -> Account | None:
        """
        Fetch an account row with a `SELECT ... FOR UPDATE` row lock held for
        the remainder of the current transaction.

        This is the cornerstone of our concurrency-safety story: any two
        concurrent transfers touching the same account will serialize on
        this lock, so read-modify-write balance updates can never race. The
        caller (service layer) is responsible for locking multiple accounts
        in a globally consistent order to avoid deadlocks -- see
        `TransferService._get_accounts_locked_in_order`.
        """
        result = await self.session.execute(
            select(Account).where(Account.id == account_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def exists(self, account_id: uuid.UUID) -> bool:
        result = await self.session.execute(select(Account.id).where(Account.id == account_id))
        return result.scalar_one_or_none() is not None
