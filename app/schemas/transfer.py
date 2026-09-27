import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.transfer import EntryDirection, TransferStatus


class TransferCreate(BaseModel):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal = Field(..., gt=0, description="Amount in major units, e.g. 25.50.")
    currency: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{3}$",
        description="Must match both accounts' currency if provided; otherwise inferred.",
    )
    description: str | None = Field(default=None, max_length=500)

    @field_validator("amount")
    @classmethod
    def validate_decimal_places(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("amount supports at most 2 decimal places")
        return v

    @field_validator("to_account_id")
    @classmethod
    def validate_distinct_accounts(cls, v: uuid.UUID, info):
        from_id = info.data.get("from_account_id")
        if from_id is not None and v == from_id:
            raise ValueError("from_account_id and to_account_id must differ")
        return v


class TransferRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal
    currency: str
    status: TransferStatus
    description: str | None
    created_at: datetime


class TransactionRead(BaseModel):
    """A single ledger entry, from the point of view of one account -- this
    is what `GET /accounts/{id}/transactions` returns."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transfer_id: uuid.UUID
    direction: EntryDirection
    amount: Decimal
    balance_after: Decimal
    currency: str
    counterparty_account_id: uuid.UUID
    created_at: datetime
