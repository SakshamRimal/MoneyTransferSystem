"""
Test doubles for the unit tests.

The service layer depends on repositories and a SQLAlchemy session; here we
replace both with hand-rolled fakes/mocks so service logic (locking order,
balance arithmetic, idempotency branching, error mapping) can be exercised
without a database.

`FakeSession` emulates the one piece of SQLAlchemy behaviour the services
rely on during a unit test: applying Python-side column defaults (id,
timestamps, version) at flush time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock

from app.models.account import Account, AccountStatus
from app.services.account_service import AccountService
from app.services.transfer_service import TransferService


def _stamp_defaults(entity: Any) -> None:
    """Apply the Python-side column defaults SQLAlchemy would apply on INSERT."""
    table = getattr(type(entity), "__table__", None)
    if table is not None:
        for column in table.columns:
            default = column.default
            if default is None or getattr(entity, column.key, None) is not None:
                continue
            # SQLAlchemy wraps Python callables as `lambda ctx: fn()`.
            value = default.arg(None) if default.is_callable else default.arg
            setattr(entity, column.key, value)
    # Server-side defaults (func.now()) never appear as Python values, so
    # timestamps are stamped manually to keep read models valid.
    for timestamp_field in ("created_at", "updated_at"):
        if getattr(entity, timestamp_field, None) is None and hasattr(
            type(entity), timestamp_field
        ):
            setattr(entity, timestamp_field, datetime.now(UTC))


class FakeSession:
    """Minimal `AsyncSession` stand-in used by the mocked repositories."""

    def __init__(self) -> None:
        self.pending: list[Any] = []
        self.flush_count = 0
        self.execute = AsyncMock(return_value=None)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    def add(self, entity: Any) -> Any:
        self.pending.append(entity)
        return entity

    async def flush(self) -> None:
        self.flush_count += 1
        for entity in self.pending:
            _stamp_defaults(entity)


def make_account(
    balance_minor_units: int,
    *,
    currency: str = "USD",
    status: AccountStatus = AccountStatus.ACTIVE,
    owner_name: str = "Alice",
) -> Account:
    now = datetime.now(UTC)
    return Account(
        id=uuid.uuid4(),
        owner_name=owner_name,
        currency=currency,
        balance_minor_units=balance_minor_units,
        status=status,
        version=0,
        created_at=now,
        updated_at=now,
    )


@dataclass
class AccountServiceHarness:
    service: AccountService
    session: FakeSession
    account_repo: Mock


def build_account_service() -> AccountServiceHarness:
    session = FakeSession()
    account_repo = Mock(name="AccountRepository")
    account_repo.add = Mock(side_effect=session.add)
    account_repo.get_by_id = AsyncMock()
    account_repo.exists = AsyncMock()
    account_repo.get_for_update = AsyncMock()
    service = AccountService(session=session, account_repo=account_repo)
    return AccountServiceHarness(service=service, session=session, account_repo=account_repo)


@dataclass
class TransferServiceHarness:
    service: TransferService
    session: FakeSession
    account_repo: Mock
    transfer_repo: Mock
    idempotency_repo: Mock


def build_transfer_service() -> TransferServiceHarness:
    session = FakeSession()

    account_repo = Mock(name="AccountRepository")
    account_repo.get_by_id = AsyncMock()
    account_repo.get_for_update = AsyncMock()
    account_repo.exists = AsyncMock()

    transfer_repo = Mock(name="TransferRepository")
    transfer_repo.add = Mock(side_effect=session.add)
    transfer_repo.add_ledger_entry = Mock(side_effect=session.add)
    transfer_repo.get_by_id = AsyncMock()
    transfer_repo.get_by_idempotency_key = AsyncMock()
    transfer_repo.list_for_account = AsyncMock(return_value=([], 0))

    idempotency_repo = Mock(name="IdempotencyRepository")
    idempotency_repo.get = AsyncMock(return_value=None)
    idempotency_repo.add = Mock(side_effect=session.add)

    service = TransferService(
        session=session,
        account_repo=account_repo,
        transfer_repo=transfer_repo,
        idempotency_repo=idempotency_repo,
    )
    return TransferServiceHarness(
        service=service,
        session=session,
        account_repo=account_repo,
        transfer_repo=transfer_repo,
        idempotency_repo=idempotency_repo,
    )
