import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.account import AccountStatus

_CURRENCY_PATTERN = r"^[A-Z]{3}$"


class AccountCreate(BaseModel):
    owner_name: str = Field(..., min_length=1, max_length=255)
    currency: str = Field(default="USD", pattern=_CURRENCY_PATTERN)
    initial_balance: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        description="Opening balance in major units, e.g. 100.00 for $100.00.",
    )

    @field_validator("owner_name")
    @classmethod
    def strip_owner_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("owner_name must not be blank")
        return v

    @field_validator("initial_balance")
    @classmethod
    def validate_decimal_places(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("initial_balance supports at most 2 decimal places")
        return v


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_name: str
    currency: str
    balance: Decimal
    status: AccountStatus
    created_at: datetime
    updated_at: datetime
