"""Unit tests for request/response schema validation."""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.account import AccountCreate
from app.schemas.transfer import TransferCreate


class TestAccountCreate:
    def test_accepts_valid_payload(self) -> None:
        payload = AccountCreate(owner_name=" Alice ", currency="USD", initial_balance="12.34")
        assert payload.owner_name == "Alice"  # stripped
        assert payload.currency == "USD"
        assert payload.initial_balance == Decimal("12.34")

    def test_defaults(self) -> None:
        payload = AccountCreate(owner_name="Bob")
        assert payload.currency == "USD"
        assert payload.initial_balance == Decimal("0.00")

    @pytest.mark.parametrize("owner_name", ["", "   "])
    def test_rejects_blank_owner_name(self, owner_name: str) -> None:
        with pytest.raises(ValidationError):
            AccountCreate(owner_name=owner_name)

    @pytest.mark.parametrize("balance", ["1.999", "0.001", "10.123"])
    def test_rejects_sub_cent_precision(self, balance: str) -> None:
        with pytest.raises(ValidationError, match="decimal places"):
            AccountCreate(owner_name="Alice", initial_balance=balance)

    @pytest.mark.parametrize("balance", ["-0.01", "-100"])
    def test_rejects_negative_balance(self, balance: str) -> None:
        with pytest.raises(ValidationError):
            AccountCreate(owner_name="Alice", initial_balance=balance)

    @pytest.mark.parametrize("currency", ["usd", "US", "DOLLARS", "U1D"])
    def test_rejects_malformed_currency(self, currency: str) -> None:
        with pytest.raises(ValidationError):
            AccountCreate(owner_name="Alice", currency=currency)


class TestTransferCreate:
    @pytest.fixture
    def from_id(self) -> str:
        return str(uuid.uuid4())

    @pytest.fixture
    def to_id(self) -> str:
        return str(uuid.uuid4())

    def test_accepts_valid_payload(self, from_id: str, to_id: str) -> None:
        payload = TransferCreate(from_account_id=from_id, to_account_id=to_id, amount="25.50")
        assert payload.amount == Decimal("25.50")
        assert payload.currency is None
        assert payload.description is None

    def test_rejects_same_account(self, from_id: str) -> None:
        with pytest.raises(ValidationError, match="must differ"):
            TransferCreate(from_account_id=from_id, to_account_id=from_id, amount="1.00")

    @pytest.mark.parametrize("amount", ["0", "0.00", "-1.00"])
    def test_rejects_non_positive_amount(self, from_id: str, to_id: str, amount: str) -> None:
        with pytest.raises(ValidationError):
            TransferCreate(from_account_id=from_id, to_account_id=to_id, amount=amount)

    def test_rejects_sub_cent_precision(self, from_id: str, to_id: str) -> None:
        with pytest.raises(ValidationError, match="decimal places"):
            TransferCreate(from_account_id=from_id, to_account_id=to_id, amount="1.001")

    @pytest.mark.parametrize("currency", ["usd", "EURO", "42X"])
    def test_rejects_malformed_currency(self, from_id: str, to_id: str, currency: str) -> None:
        with pytest.raises(ValidationError):
            TransferCreate(
                from_account_id=from_id, to_account_id=to_id, amount="1.00", currency=currency
            )

    def test_rejects_overlong_description(self, from_id: str, to_id: str) -> None:
        with pytest.raises(ValidationError):
            TransferCreate(
                from_account_id=from_id,
                to_account_id=to_id,
                amount="1.00",
                description="x" * 501,
            )
