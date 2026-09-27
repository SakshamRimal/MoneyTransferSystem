from fastapi import APIRouter, Header, Response, status

from app.api.deps import TransferServiceDep
from app.core.logging import get_logger
from app.schemas.common import ErrorResponse
from app.schemas.transfer import TransferCreate, TransferRead

router = APIRouter(prefix="/transfers", tags=["transfers"])
logger = get_logger(__name__)


@router.post(
    "",
    response_model=TransferRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Transfer money between two accounts",
    description=(
        "Moves `amount` from `from_account_id` to `to_account_id` atomically. "
        "Supply an `Idempotency-Key` header to make retries safe: replaying "
        "the same key with the same request body returns the original result "
        "without re-executing the transfer; reusing the key with a different "
        "body returns 409."
    ),
)
async def create_transfer(
    payload: TransferCreate,
    response: Response,
    service: TransferServiceDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TransferRead:
    transfer_read, status_code, was_replayed = await service.create_transfer(
        payload, idempotency_key
    )
    response.status_code = status_code
    if was_replayed:
        response.headers["Idempotent-Replay"] = "true"
    return transfer_read
