"""
Account model.

Money is stored as an integer number of "minor units" (e.g. cents for USD)
rather than float or even Decimal-backed NUMERIC columns. Integers avoid any
possibility of binary floating point error and avoid subtle Decimal
precision/rounding configuration mistakes — arithmetic is always exact.
Conversion to/from a human "1.00"-style Decimal happens only at the API
boundary (schemas), never inside business logic.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("balance_minor_units >= 0", name="ck_accounts_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status", native_enum=True),
        nullable=False,
        default=AccountStatus.ACTIVE,
    )

    # Optimistic-concurrency guard used *in addition to* row-level locking
    # (SELECT ... FOR UPDATE) during transfers. Incremented on every balance
    # mutation. Not strictly required given we always take a row lock before
    # mutating balances, but kept as defense-in-depth / an audit signal and
    # because it costs nothing.
    version: Mapped[int] = mapped_column(nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Account id={self.id} owner={self.owner_name!r} "
            f"balance={self.balance_minor_units} {self.currency} status={self.status}>"
        )
