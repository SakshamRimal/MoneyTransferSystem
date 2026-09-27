import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AccountNotFoundError
from app.core.logging import get_logger
from app.models.account import Account
from app.repositories.account_repository import AccountRepository
from app.schemas.account import AccountCreate, AccountRead
from app.utils.money import to_major_units, to_minor_units

logger = get_logger(__name__)


class AccountService:
    """Business logic for account creation/lookup. Deliberately thin: there
    is little business logic beyond input->entity mapping and existence
    checks, so most of the interesting work lives in TransferService."""

    def __init__(self, session: AsyncSession, account_repo: AccountRepository):
        self.session = session
        self.account_repo = account_repo

    async def create_account(self, payload: AccountCreate) -> AccountRead:
        account = Account(
            owner_name=payload.owner_name,
            currency=payload.currency,
            balance_minor_units=to_minor_units(payload.initial_balance),
        )
        self.account_repo.add(account)
        await self.session.flush()
        logger.info(
            "account_created",
            extra={
                "account_id": str(account.id),
                "currency": account.currency,
                "initial_balance_minor_units": account.balance_minor_units,
            },
        )
        return self._to_read_model(account)

    async def get_account(self, account_id: uuid.UUID) -> AccountRead:
        account = await self.account_repo.get_by_id(account_id)
        if account is None:
            raise AccountNotFoundError(str(account_id))
        return self._to_read_model(account)

    @staticmethod
    def _to_read_model(account: Account) -> AccountRead:
        return AccountRead(
            id=account.id,
            owner_name=account.owner_name,
            currency=account.currency,
            balance=to_major_units(account.balance_minor_units),
            status=account.status,
            created_at=account.created_at,
            updated_at=account.updated_at,
        )
