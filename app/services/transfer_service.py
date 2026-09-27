"""
Transfer service.

This is the heart of the system: moving money between two accounts
correctly under concurrent load, with idempotent retries. Two distinct
concurrency problems are handled here, deliberately with different tools:

1. Two *different* transfers racing on the *same account(s)*
   ---------------------------------------------------------
   Solved with row-level locking: `SELECT ... FOR UPDATE` (see
   `AccountRepository.get_for_update`) taken on both accounts, always in a
   fixed global order (sorted by UUID) regardless of transfer direction.
   Fixed lock ordering is what prevents deadlocks -- without it, transfer
   A->B and a concurrent transfer B->A could each hold one lock and wait
   forever for the other.

2. The *same* transfer request being retried (client timeout, network
   blip, at-least-once delivery, double-click, etc.)
   ---------------------------------------------------------
   Solved with an `Idempotency-Key` header + a Postgres advisory
   transaction lock (`pg_advisory_xact_lock`) keyed on that header value.
   The advisory lock serializes concurrent requests carrying the *same* key
   so only one of them ever executes the business logic; the loser(s) block
   until the winner commits, then replay its cached response. This is
   stronger than a bare unique-constraint-and-catch approach because it
   prevents the money from moving twice in the first place, rather than
   letting both racers execute and cleaning up after the fact.
"""

import hashlib
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountInactiveError,
    AccountNotFoundError,
    CurrencyMismatchError,
    IdempotencyKeyConflictError,
    InsufficientFundsError,
    SameAccountTransferError,
)
from app.core.logging import get_logger
from app.models.account import AccountStatus
from app.models.idempotency import IdempotencyKey
from app.models.transfer import EntryDirection, LedgerEntry, Transfer, TransferStatus
from app.repositories.account_repository import AccountRepository
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.transfer_repository import TransferRepository
from app.schemas.common import Page, PageMeta
from app.schemas.transfer import TransactionRead, TransferCreate, TransferRead
from app.utils.money import to_major_units, to_minor_units

logger = get_logger(__name__)

IDEMPOTENCY_ENDPOINT = "POST /api/v1/transfers"


class TransferService:
    def __init__(
        self,
        session: AsyncSession,
        account_repo: AccountRepository,
        transfer_repo: TransferRepository,
        idempotency_repo: IdempotencyRepository,
    ):
        self.session = session
        self.account_repo = account_repo
        self.transfer_repo = transfer_repo
        self.idempotency_repo = idempotency_repo

    # -- public API ---------------------------------------------------

    async def create_transfer(
        self, payload: TransferCreate, idempotency_key: str | None
    ) -> tuple[TransferRead, int, bool]:
        """
        Execute a transfer, honoring an optional Idempotency-Key.

        Returns (transfer_read, http_status_code, was_replayed).
        """
        fingerprint = self._fingerprint(payload)

        if idempotency_key:
            await self._acquire_idempotency_lock(idempotency_key)
            existing = await self.idempotency_repo.get(idempotency_key, IDEMPOTENCY_ENDPOINT)
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyKeyConflictError(
                        "This Idempotency-Key was already used with a different " "request payload."
                    )
                logger.info(
                    "idempotent_replay",
                    extra={"idempotency_key": idempotency_key},
                )
                return (
                    TransferRead.model_validate(existing.response_body),
                    existing.response_status_code,
                    True,
                )

        transfer = await self._execute_transfer(payload)
        transfer_read = self._to_read_model(transfer)
        status_code = 201

        if idempotency_key:
            self.idempotency_repo.add(
                IdempotencyKey(
                    key=idempotency_key,
                    endpoint=IDEMPOTENCY_ENDPOINT,
                    request_fingerprint=fingerprint,
                    response_status_code=status_code,
                    response_body=json.loads(transfer_read.model_dump_json()),
                )
            )
            await self.session.flush()

        return transfer_read, status_code, False

    async def list_account_transactions(
        self, account_id: uuid.UUID, limit: int, offset: int
    ) -> Page[TransactionRead]:
        if not await self.account_repo.exists(account_id):
            raise AccountNotFoundError(str(account_id))

        entries, total = await self.transfer_repo.list_for_account(account_id, limit, offset)
        items = [
            TransactionRead(
                id=e.id,
                transfer_id=e.transfer_id,
                direction=e.direction,
                amount=to_major_units(e.amount_minor_units),
                balance_after=to_major_units(e.balance_after_minor_units),
                currency=e.currency,
                counterparty_account_id=e.counterparty_account_id,
                created_at=e.created_at,
            )
            for e in entries
        ]
        return Page[TransactionRead](
            items=items,
            meta=PageMeta(
                total=total,
                limit=limit,
                offset=offset,
                has_more=offset + len(items) < total,
            ),
        )

    # -- internals ------------------------------------------------------

    async def _acquire_idempotency_lock(self, idempotency_key: str) -> None:
        """
        Block until we hold an exclusive Postgres advisory lock scoped to
        this (endpoint, key) pair. Automatically released at transaction
        end (commit or rollback) -- see `pg_advisory_xact_lock` docs.
        """
        lock_name = f"{IDEMPOTENCY_ENDPOINT}:{idempotency_key}"
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_name)::bigint)"),
            {"lock_name": lock_name},
        )

    async def _execute_transfer(self, payload: TransferCreate) -> Transfer:
        if payload.from_account_id == payload.to_account_id:
            raise SameAccountTransferError()

        from_account, to_account = await self._get_accounts_locked_in_order(
            payload.from_account_id, payload.to_account_id
        )

        if from_account.status != AccountStatus.ACTIVE:
            raise AccountInactiveError(str(from_account.id))
        if to_account.status != AccountStatus.ACTIVE:
            raise AccountInactiveError(str(to_account.id))

        if from_account.currency != to_account.currency:
            raise CurrencyMismatchError(
                "from_account and to_account must be denominated in the same currency."
            )
        currency = payload.currency or from_account.currency
        if currency != from_account.currency:
            raise CurrencyMismatchError(
                f"Requested currency '{currency}' does not match the accounts' "
                f"currency '{from_account.currency}'."
            )

        amount_minor = to_minor_units(payload.amount)

        if from_account.balance_minor_units < amount_minor:
            raise InsufficientFundsError(str(from_account.id))

        from_account.balance_minor_units -= amount_minor
        from_account.version += 1
        to_account.balance_minor_units += amount_minor
        to_account.version += 1

        transfer = Transfer(
            from_account_id=from_account.id,
            to_account_id=to_account.id,
            amount_minor_units=amount_minor,
            currency=currency,
            status=TransferStatus.COMPLETED,
            description=payload.description,
        )
        self.transfer_repo.add(transfer)
        # Flush now so `transfer.id` / `transfer.created_at` (server default)
        # are populated before we build ledger entries and the response.
        await self.session.flush()

        self.transfer_repo.add_ledger_entry(
            LedgerEntry(
                account_id=from_account.id,
                transfer_id=transfer.id,
                direction=EntryDirection.DEBIT,
                amount_minor_units=amount_minor,
                balance_after_minor_units=from_account.balance_minor_units,
                currency=currency,
                counterparty_account_id=to_account.id,
            )
        )
        self.transfer_repo.add_ledger_entry(
            LedgerEntry(
                account_id=to_account.id,
                transfer_id=transfer.id,
                direction=EntryDirection.CREDIT,
                amount_minor_units=amount_minor,
                balance_after_minor_units=to_account.balance_minor_units,
                currency=currency,
                counterparty_account_id=from_account.id,
            )
        )
        await self.session.flush()

        logger.info(
            "transfer_completed",
            extra={
                "transfer_id": str(transfer.id),
                "from_account_id": str(from_account.id),
                "to_account_id": str(to_account.id),
                "amount_minor_units": amount_minor,
                "currency": currency,
            },
        )
        return transfer

    async def _get_accounts_locked_in_order(self, from_id: uuid.UUID, to_id: uuid.UUID):
        """Lock both accounts FOR UPDATE in a fixed global order (by UUID
        value) so that two transfers moving money in opposite directions
        between the same pair of accounts can never deadlock each other."""
        first_id, second_id = sorted([from_id, to_id])
        first_account = await self.account_repo.get_for_update(first_id)
        second_account = await self.account_repo.get_for_update(second_id)

        accounts_by_id = {}
        if first_account is not None:
            accounts_by_id[first_account.id] = first_account
        if second_account is not None:
            accounts_by_id[second_account.id] = second_account

        from_account = accounts_by_id.get(from_id)
        to_account = accounts_by_id.get(to_id)

        if from_account is None:
            raise AccountNotFoundError(str(from_id))
        if to_account is None:
            raise AccountNotFoundError(str(to_id))

        return from_account, to_account

    @staticmethod
    def _fingerprint(payload: TransferCreate) -> str:
        canonical = json.dumps(
            {
                "from_account_id": str(payload.from_account_id),
                "to_account_id": str(payload.to_account_id),
                "amount": str(payload.amount),
                "currency": payload.currency,
                "description": payload.description,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _to_read_model(transfer: Transfer) -> TransferRead:
        return TransferRead(
            id=transfer.id,
            from_account_id=transfer.from_account_id,
            to_account_id=transfer.to_account_id,
            amount=to_major_units(transfer.amount_minor_units),
            currency=transfer.currency,
            status=transfer.status,
            description=transfer.description,
            created_at=transfer.created_at,
        )
