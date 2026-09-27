"""Unit tests for `AccountService` with a mocked repository."""

import uuid
from decimal import Decimal

import pytest

from app.core.exceptions import AccountNotFoundError
from app.models.account import AccountStatus
from app.schemas.account import AccountCreate
from tests.unit.fakes import build_account_service, make_account


@pytest.fixture
def harness():
    return build_account_service()


class TestCreateAccount:
    async def test_creates_account_with_minor_unit_balance(self, harness) -> None:
        payload = AccountCreate(owner_name=" Alice ", currency="USD", initial_balance="123.45")

        read = await harness.service.create_account(payload)

        added = harness.account_repo.add.call_args.args[0]
        assert added.owner_name == "Alice"
        assert added.currency == "USD"
        assert added.balance_minor_units == 12345
        assert added.status == AccountStatus.ACTIVE
        assert harness.session.flush_count == 1

        assert read.id is not None
        assert read.balance == Decimal("123.45")
        assert read.owner_name == "Alice"
        assert read.status == AccountStatus.ACTIVE

    async def test_zero_balance_by_default(self, harness) -> None:
        read = await harness.service.create_account(AccountCreate(owner_name="Bob"))

        added = harness.account_repo.add.call_args.args[0]
        assert added.balance_minor_units == 0
        assert read.balance == Decimal("0.00")


class TestGetAccount:
    async def test_returns_mapped_account(self, harness) -> None:
        account = make_account(9999, owner_name="Carol", currency="EUR")
        harness.account_repo.get_by_id.return_value = account

        read = await harness.service.get_account(account.id)

        harness.account_repo.get_by_id.assert_awaited_once_with(account.id)
        assert read.id == account.id
        assert read.balance == Decimal("99.99")
        assert read.currency == "EUR"
        assert read.owner_name == "Carol"

    async def test_unknown_account_raises_domain_error(self, harness) -> None:
        missing = uuid.uuid4()
        harness.account_repo.get_by_id.return_value = None

        with pytest.raises(AccountNotFoundError) as excinfo:
            await harness.service.get_account(missing)

        assert excinfo.value.code == "account_not_found"
        assert excinfo.value.http_status == 404
        assert str(missing) in excinfo.value.message
        assert harness.account_repo.get_by_id.await_count == 1


class TestCreateAccountErrors:
    async def test_repository_error_propagates(self, harness) -> None:
        harness.account_repo.add.side_effect = RuntimeError("db down")
        with pytest.raises(RuntimeError, match="db down"):
            await harness.service.create_account(AccountCreate(owner_name="Bob"))
