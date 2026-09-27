"""Unit tests for `TransferService` with mocked repositories.

Covers the business rules: locking order, balance arithmetic, currency and
status validation, idempotent replay/conflict, and history pagination --
all without a database.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import (
    AccountInactiveError,
    AccountNotFoundError,
    CurrencyMismatchError,
    IdempotencyKeyConflictError,
    InsufficientFundsError,
    SameAccountTransferError,
)
from app.models.account import AccountStatus
from app.models.idempotency import IdempotencyKey
from app.models.transfer import EntryDirection, LedgerEntry, Transfer, TransferStatus
from app.schemas.transfer import TransferCreate
from app.services.transfer_service import IDEMPOTENCY_ENDPOINT, TransferService
from tests.unit.fakes import FakeSession, build_transfer_service, make_account


def transfer_payload(
    from_account, to_account, amount: str = "10.00", currency: str | None = None
) -> TransferCreate:
    return TransferCreate(
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        amount=Decimal(amount),
        currency=currency,
    )


@pytest.fixture
def harness():
    return build_transfer_service()


@pytest.fixture
def source():
    return make_account(10_000, owner_name="Source")  # 100.00


@pytest.fixture
def destination():
    return make_account(500, owner_name="Destination")  # 5.00


def lock_both(harness, *accounts) -> None:
    """Wire `get_for_update` to return the given accounts (sorted-lock order)."""
    by_id = {account.id: account for account in accounts}
    harness.account_repo.get_for_update = AsyncMock(side_effect=lambda i: by_id.get(i))


class TestHappyPath:
    async def test_moves_money_and_writes_double_entry_ledger(
        self, harness, source, destination
    ) -> None:
        lock_both(harness, source, destination)
        payload = transfer_payload(source, destination, amount="40.00")

        read, status_code, was_replayed = await harness.service.create_transfer(payload, None)

        assert status_code == 201
        assert was_replayed is False
        assert source.balance_minor_units == 6_000  # 100.00 - 40.00
        assert destination.balance_minor_units == 4_500  # 5.00 + 40.00
        assert source.version == 1
        assert destination.version == 1

        transfers = [call.args[0] for call in harness.transfer_repo.add.call_args_list]
        entries = [call.args[0] for call in harness.transfer_repo.add_ledger_entry.call_args_list]
        assert len(transfers) == 1
        assert transfers[0].amount_minor_units == 4_000
        assert transfers[0].currency == "USD"
        assert len(entries) == 2
        assert {entry.direction for entry in entries} == {
            EntryDirection.DEBIT,
            EntryDirection.CREDIT,
        }
        assert read.amount == Decimal("40.00")
        assert read.from_account_id == source.id
        assert read.to_account_id == destination.id

    async def test_locks_accounts_in_consistent_order_regardless_of_direction(
        self, harness, source, destination
    ) -> None:
        """A->B and B->A must both lock in sorted-UUID order (deadlock avoidance)."""
        first_id, second_id = sorted([source.id, destination.id])
        by_id = {source.id: source, destination.id: destination}
        harness.account_repo.get_for_update = AsyncMock(side_effect=lambda i: by_id[i])

        # Direct A->B
        await harness.service.create_transfer(transfer_payload(source, destination), None)
        order_ab = [c.args[0] for c in harness.account_repo.get_for_update.call_args_list]
        assert order_ab == [first_id, second_id]

        # And the reverse B->A uses the exact same order.
        harness.account_repo.get_for_update.reset_mock()
        await harness.service.create_transfer(transfer_payload(destination, source), None)
        order_ba = [c.args[0] for c in harness.account_repo.get_for_update.call_args_list]
        assert order_ba == [first_id, second_id]

    async def test_infers_currency_from_accounts_when_not_supplied(
        self, harness, source, destination
    ) -> None:
        lock_both(harness, source, destination)
        payload = transfer_payload(source, destination, amount="1.00", currency=None)

        read, _, _ = await harness.service.create_transfer(payload, None)

        assert read.currency == "USD"


class TestValidationErrors:
    async def test_same_account_rejected(self, harness, source) -> None:
        # The schema validator normally rejects this first; the service keeps
        # its own guard as defence in depth, exercised here via model_construct.
        payload = TransferCreate.model_construct(
            from_account_id=source.id,
            to_account_id=source.id,
            amount=Decimal("1.00"),
            currency=None,
            description=None,
        )

        with pytest.raises(SameAccountTransferError) as excinfo:
            await harness.service.create_transfer(payload, None)

        assert excinfo.value.http_status == 422
        harness.account_repo.get_for_update.assert_not_awaited()

    async def test_unknown_source_account(self, harness, destination) -> None:
        missing = make_account(0)
        harness.account_repo.get_for_update = AsyncMock(return_value=None)

        with pytest.raises(AccountNotFoundError):
            await harness.service.create_transfer(transfer_payload(missing, destination), None)

    async def test_unknown_destination_account(self, harness, source) -> None:
        missing = make_account(0)

        async def only_source(account_id):
            return source if account_id == source.id else None

        harness.account_repo.get_for_update = AsyncMock(side_effect=only_source)

        with pytest.raises(AccountNotFoundError):
            await harness.service.create_transfer(transfer_payload(source, missing), None)

    async def test_insufficient_funds_leaves_state_untouched(
        self, harness, source, destination
    ) -> None:
        lock_both(harness, source, destination)
        before_source = source.balance_minor_units
        before_destination = destination.balance_minor_units

        with pytest.raises(InsufficientFundsError) as excinfo:
            await harness.service.create_transfer(
                transfer_payload(source, destination, amount="100.01"), None
            )

        assert excinfo.value.http_status == 422
        assert source.balance_minor_units == before_source
        assert destination.balance_minor_units == before_destination
        harness.transfer_repo.add.assert_not_called()
        harness.transfer_repo.add_ledger_entry.assert_not_called()

    async def test_accounts_in_different_currencies_rejected(self, harness, source) -> None:
        eur_destination = make_account(100, currency="EUR")
        lock_both(harness, source, eur_destination)

        with pytest.raises(CurrencyMismatchError):
            await harness.service.create_transfer(
                transfer_payload(source, eur_destination, amount="1.00"), None
            )

    async def test_payload_currency_must_match_accounts(self, harness, source, destination) -> None:
        lock_both(harness, source, destination)

        with pytest.raises(CurrencyMismatchError):
            await harness.service.create_transfer(
                transfer_payload(source, destination, amount="1.00", currency="EUR"), None
            )

    async def test_inactive_account_rejected(self, harness, destination) -> None:
        frozen = make_account(10_000, status=AccountStatus.FROZEN)
        lock_both(harness, frozen, destination)

        with pytest.raises(AccountInactiveError):
            await harness.service.create_transfer(
                transfer_payload(frozen, destination, amount="1.00"), None
            )


class TestIdempotency:
    async def test_replay_returns_stored_response_without_reExecuting(
        self, harness, source, destination
    ) -> None:
        lock_both(harness, source, destination)
        stored_body = {
            "id": str(uuid.uuid4()),
            "from_account_id": str(source.id),
            "to_account_id": str(destination.id),
            "amount": "40.00",
            "currency": "USD",
            "status": "completed",
            "description": None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        fingerprint = TransferService._fingerprint(
            transfer_payload(source, destination, amount="40.00")
        )
        harness.idempotency_repo.get.return_value = IdempotencyKey(
            key="key-1",
            endpoint=IDEMPOTENCY_ENDPOINT,
            request_fingerprint=fingerprint,
            response_status_code=201,
            response_body=stored_body,
        )

        read, status_code, was_replayed = await harness.service.create_transfer(
            transfer_payload(source, destination, amount="40.00"), "key-1"
        )

        assert was_replayed is True
        assert status_code == 201
        assert str(read.id) == stored_body["id"]
        # Business logic must not have run again: no locking, no transfer row.
        harness.account_repo.get_for_update.assert_not_awaited()
        harness.transfer_repo.add.assert_not_called()
        harness.idempotency_repo.add.assert_not_called()

    async def test_same_key_different_payload_conflicts(self, harness, source, destination) -> None:
        lock_both(harness, source, destination)
        harness.idempotency_repo.get.return_value = IdempotencyKey(
            key="key-1",
            endpoint=IDEMPOTENCY_ENDPOINT,
            request_fingerprint="a-different-fingerprint",
            response_status_code=201,
            response_body={},
        )

        with pytest.raises(IdempotencyKeyConflictError) as excinfo:
            await harness.service.create_transfer(
                transfer_payload(source, destination, amount="40.00"), "key-1"
            )

        assert excinfo.value.http_status == 409
        harness.transfer_repo.add.assert_not_called()

    async def test_first_use_acquires_lock_and_stores_response(
        self, harness, source, destination
    ) -> None:
        lock_both(harness, source, destination)

        await harness.service.create_transfer(
            transfer_payload(source, destination, amount="10.00"), "key-1"
        )

        # Advisory lock taken before any business logic ran.
        harness.session.execute.assert_awaited()
        lock_sql = harness.session.execute.await_args.args[0]
        assert "pg_advisory_xact_lock" in str(lock_sql)

        stored = harness.idempotency_repo.add.call_args.args[0]
        assert stored.key == "key-1"
        assert stored.endpoint == IDEMPOTENCY_ENDPOINT
        assert stored.response_status_code == 201

    async def test_failed_transfer_is_not_memorized(self, harness, source, destination) -> None:
        """A rejected transfer must not poison the idempotency key."""
        lock_both(harness, source, destination)

        with pytest.raises(InsufficientFundsError):
            await harness.service.create_transfer(
                transfer_payload(source, destination, amount="9999.99"), "key-1"
            )

        harness.idempotency_repo.add.assert_not_called()


class TestFingerprint:
    def test_identical_payloads_share_fingerprint(self) -> None:
        first = make_account(0)
        second = make_account(0)
        assert TransferService._fingerprint(
            transfer_payload(first, second, amount="10.00")
        ) == TransferService._fingerprint(transfer_payload(first, second, amount="10.00"))

    @pytest.mark.parametrize(
        "change",
        [
            {"amount": "10.01"},
            {"currency": "EUR"},
            {"description": "different"},
        ],
    )
    def test_any_payload_change_changes_fingerprint(self, change) -> None:
        first = make_account(0)
        second = make_account(0)
        base = transfer_payload(first, second, amount="10.00")
        other = transfer_payload(first, second, amount="10.00", currency=None)
        for key, value in change.items():
            setattr(other, key, value if key != "amount" else Decimal(value))
        assert TransferService._fingerprint(base) != TransferService._fingerprint(other)


class TestListAccountTransactions:
    async def test_unknown_account_raises(self, harness) -> None:
        harness.account_repo.exists.return_value = False
        with pytest.raises(AccountNotFoundError):
            await harness.service.list_account_transactions(uuid.uuid4(), limit=10, offset=0)

    async def test_maps_ledger_entries_and_pagination_meta(self, harness) -> None:
        account = make_account(100)
        harness.account_repo.exists.return_value = True
        entry = LedgerEntry(
            id=uuid.uuid4(),
            account_id=account.id,
            transfer_id=uuid.uuid4(),
            direction=EntryDirection.DEBIT,
            amount_minor_units=2_550,
            balance_after_minor_units=7_450,
            currency="USD",
            counterparty_account_id=uuid.uuid4(),
            created_at=datetime.now(UTC),
        )
        harness.transfer_repo.list_for_account.return_value = ([entry], 7)

        page = await harness.service.list_account_transactions(account.id, limit=3, offset=0)

        harness.transfer_repo.list_for_account.assert_awaited_once_with(account.id, 3, 0)
        assert page.meta.total == 7
        assert page.meta.limit == 3
        assert page.meta.offset == 0
        assert page.meta.has_more is True
        item = page.items[0]
        assert item.amount == Decimal("25.50")
        assert item.balance_after == Decimal("74.50")
        assert item.direction == EntryDirection.DEBIT

    async def test_last_page_has_no_more(self, harness) -> None:
        account = make_account(100)
        harness.account_repo.exists.return_value = True
        harness.transfer_repo.list_for_account.return_value = ([], 6)

        page = await harness.service.list_account_transactions(account.id, limit=3, offset=6)

        assert page.items == []
        assert page.meta.has_more is False


async def test_transfer_status_default_is_applied_at_flush_time() -> None:
    """Python-side column defaults are applied by the unit of work at flush."""
    session = FakeSession()
    transfer = Transfer(
        from_account_id=uuid.uuid4(),
        to_account_id=uuid.uuid4(),
        amount_minor_units=1,
        currency="USD",
    )
    assert transfer.status is None
    assert transfer.id is None

    session.add(transfer)
    await session.flush()

    assert transfer.status == TransferStatus.COMPLETED
    assert transfer.id is not None
    assert transfer.created_at is not None
