"""Integration tests for POST /transfers: happy path and business-rule errors."""

import uuid

from tests.support import API, create_account, error_code, get_balance, transfer


async def test_transfer_moves_money_and_writes_ledger_entries(client) -> None:
    alice = await create_account(client, owner_name="Alice", initial_balance="100.00")
    bob = await create_account(client, owner_name="Bob", initial_balance="5.00")

    response = await transfer(client, alice, bob, "25.50", currency="USD")

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["from_account_id"] == alice
    assert body["to_account_id"] == bob
    assert body["amount"] == "25.50"
    assert body["currency"] == "USD"
    assert body["status"] == "completed"

    assert await get_balance(client, alice) == "74.50"
    assert await get_balance(client, bob) == "30.50"

    # Double-entry bookkeeping: one debit + one credit, both from the
    # counterparty's point of view, with a running balance.
    alice_history = await client.get(f"{API}/accounts/{alice}/transactions")
    bob_history = await client.get(f"{API}/accounts/{bob}/transactions")
    alice_entries = alice_history.json()["items"]
    bob_entries = bob_history.json()["items"]

    assert len(alice_entries) == 1
    assert len(bob_entries) == 1
    assert alice_entries[0]["direction"] == "debit"
    assert alice_entries[0]["amount"] == "25.50"
    assert alice_entries[0]["balance_after"] == "74.50"
    assert alice_entries[0]["counterparty_account_id"] == bob
    assert bob_entries[0]["direction"] == "credit"
    assert bob_entries[0]["balance_after"] == "30.50"
    assert alice_entries[0]["transfer_id"] == bob_entries[0]["transfer_id"]


async def test_transfer_amount_is_inferred_from_account_currency(client) -> None:
    alice = await create_account(client, currency="EUR", initial_balance="10.00")
    bob = await create_account(client, currency="EUR", initial_balance="0.00")

    response = await transfer(client, alice, bob, "1.00")

    assert response.status_code == 201
    assert response.json()["currency"] == "EUR"


async def test_insufficient_funds_rejected_and_balances_untouched(client) -> None:
    alice = await create_account(client, initial_balance="10.00")
    bob = await create_account(client, initial_balance="0.00")

    response = await transfer(client, alice, bob, "10.01")

    assert response.status_code == 422
    assert error_code(response) == "insufficient_funds"
    assert await get_balance(client, alice) == "10.00"
    assert await get_balance(client, bob) == "0.00"

    history = await client.get(f"{API}/accounts/{alice}/transactions")
    assert history.json()["meta"]["total"] == 0


async def test_exact_balance_transfer_is_allowed(client) -> None:
    alice = await create_account(client, initial_balance="10.00")
    bob = await create_account(client, initial_balance="0.00")

    response = await transfer(client, alice, bob, "10.00")

    assert response.status_code == 201
    assert await get_balance(client, alice) == "0.00"
    assert await get_balance(client, bob) == "10.00"


async def test_transfer_between_unknown_accounts_returns_404(client) -> None:
    response = await transfer(client, str(uuid.uuid4()), str(uuid.uuid4()), "1.00")

    assert response.status_code == 404
    assert error_code(response) == "account_not_found"


async def test_currency_mismatch_between_accounts(client) -> None:
    usd_account = await create_account(client, currency="USD", initial_balance="10.00")
    eur_account = await create_account(client, currency="EUR", initial_balance="10.00")

    response = await transfer(client, usd_account, eur_account, "1.00")

    assert response.status_code == 422
    assert error_code(response) == "currency_mismatch"


async def test_explicit_currency_must_match_accounts(client) -> None:
    alice = await create_account(client, currency="USD", initial_balance="10.00")
    bob = await create_account(client, currency="USD", initial_balance="10.00")

    response = await transfer(client, alice, bob, "1.00", currency="EUR")

    assert response.status_code == 422
    assert error_code(response) == "currency_mismatch"


async def test_same_account_transfer_rejected(client) -> None:
    alice = await create_account(client, initial_balance="10.00")

    response = await transfer(client, alice, alice, "1.00")

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert await get_balance(client, alice) == "10.00"


async def test_non_positive_amount_rejected(client) -> None:
    alice = await create_account(client, initial_balance="10.00")
    bob = await create_account(client, initial_balance="0.00")

    response = await transfer(client, alice, bob, "0.00")

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


async def test_sub_cent_amount_rejected(client) -> None:
    alice = await create_account(client, initial_balance="10.00")
    bob = await create_account(client, initial_balance="0.00")

    response = await transfer(client, alice, bob, "1.001")

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


async def test_sequential_transfers_accumulate_correctly(client) -> None:
    alice = await create_account(client, initial_balance="50.00")
    bob = await create_account(client, initial_balance="0.00")

    for amount in ("10.00", "15.50", "0.49"):
        response = await transfer(client, alice, bob, amount)
        assert response.status_code == 201, response.text

    assert await get_balance(client, alice) == "24.01"
    assert await get_balance(client, bob) == "25.99"

    history = await client.get(f"{API}/accounts/{bob}/transactions")
    assert history.json()["meta"]["total"] == 3
