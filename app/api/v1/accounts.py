import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import AccountServiceDep, TransferServiceDep
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.account import AccountCreate, AccountRead
from app.schemas.common import ErrorResponse, Page
from app.schemas.transfer import TransactionRead

router = APIRouter(prefix="/accounts", tags=["accounts"])
logger = get_logger(__name__)
settings = get_settings()


@router.post(
    "",
    response_model=AccountRead,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorResponse}},
    summary="Create a new account",
)
async def create_account(payload: AccountCreate, service: AccountServiceDep) -> AccountRead:
    return await service.create_account(payload)


@router.get(
    "/{account_id}",
    response_model=AccountRead,
    responses={404: {"model": ErrorResponse}},
    summary="Get account details",
)
async def get_account(account_id: uuid.UUID, service: AccountServiceDep) -> AccountRead:
    return await service.get_account(account_id)


@router.get(
    "/{account_id}/transactions",
    response_model=Page[TransactionRead],
    responses={404: {"model": ErrorResponse}},
    summary="Get an account's transaction (ledger entry) history, paginated",
)
async def get_account_transactions(
    account_id: uuid.UUID,
    transfer_service: TransferServiceDep,
    limit: int = Query(
        default=settings.default_transaction_history_page_size,
        ge=1,
        le=settings.max_transaction_history_page_size,
    ),
    offset: int = Query(default=0, ge=0),
) -> Page[TransactionRead]:
    return await transfer_service.list_account_transactions(account_id, limit, offset)
