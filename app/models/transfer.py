"""
Transfer and LedgerEntry models.

A `Transfer` is the user-facing record of "move X from A to B". Internally
it is backed by exactly two `LedgerEntry` rows (a debit on the source
account and a credit on the destination account) — classic double-entry
bookkeeping. This gives us:

  * A natural, race-free way to answer "what is this account's transaction
    history" (query ledger_entries by account_id) that scales with proper
    indexes and pagination.
  * An audit trail that is append-only: entries are never mutated or
    deleted, only ever inserted inside the same DB transaction as the
    balance update.
  * A cross-check: the sum of ledger entries for an account should always
    reconcile with `accounts.balance_minor_units`.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# `now()` in Postgres is the *transaction* timestamp, which would give every
# row written in the same transaction an identical created_at and make the
# paginated history ordering ambiguous. `clock_timestamp()` returns the real
# wall-clock time at the moment each row is written, so insertion order is
# preserved even within one transaction (which is what history pagination
# and the `created_at DESC, id DESC` sort rely on).


class TransferStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class EntryDirection(str, enum.Enum):
    DEBIT = "debit"  # money leaving an account
    CREDIT = "credit"  # money entering an account


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount_minor_units > 0", name="ck_transfers_amount_positive"),
        CheckConstraint("from_account_id != to_account_id", name="ck_transfers_distinct_accounts"),
        # A given idempotency key must map to at most one transfer.
        UniqueConstraint("idempotency_key", name="uq_transfers_idempotency_key"),
        Index("ix_transfers_from_account_id_created_at", "from_account_id", "created_at"),
        Index("ix_transfers_to_account_id_created_at", "to_account_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    to_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus, name="transfer_status", native_enum=True),
        nullable=False,
        default=TransferStatus.COMPLETED,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Transfer id={self.id} {self.from_account_id}->{self.to_account_id} "
            f"amount={self.amount_minor_units} {self.currency} status={self.status}>"
        )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_entries_account_id_created_at", "account_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transfers.id"), nullable=False
    )
    direction: Mapped[EntryDirection] = mapped_column(
        Enum(EntryDirection, name="entry_direction", native_enum=True), nullable=False
    )
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    counterparty_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LedgerEntry account={self.account_id} {self.direction} "
            f"{self.amount_minor_units} balance_after={self.balance_after_minor_units}>"
        )
