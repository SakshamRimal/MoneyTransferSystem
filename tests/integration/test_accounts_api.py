"""Integration tests for the accounts endpoints (real API + real Postgres)."""

import uuid

from tests.support import API, create_account, error_code, get_balance


async def test_create_account_returns_201_with_initial_balance(client) -> None:
    response = await client.post(
        f"{API}/accounts",
        json={"owner_name": "Alice", "currency": "USD", "initial_balance": "125.50"},
    )

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["owner_name"] == "Alice"
    assert body["currency"] == "USD"
    assert body["balance"] == "125.50"
    assert body["status"] == "active"
    assert body["created_at"] and body["updated_at"]


async def test_create_account_defaults_to_zero_balance(client) -> None:
    response = await client.post(f"{API}/accounts", json={"owner_name": "Bob"})

    assert response.status_code == 201
    assert response.json()["balance"] == "0.00"
    assert response.json()["currency"] == "USD"


async def test_create_account_rejects_sub_cent_precision(client) -> None:
    response = await client.post(
        f"{API}/accounts",
        json={"owner_name": "Alice", "initial_balance": "1.999"},
    )

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert "decimal places" in response.json()["error"]["message"]


async def test_create_account_rejects_blank_owner(client) -> None:
    response = await client.post(f"{API}/accounts", json={"owner_name": "   ", "currency": "USD"})

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


async def test_create_account_rejects_malformed_currency(client) -> None:
    response = await client.post(f"{API}/accounts", json={"owner_name": "Alice", "currency": "usd"})

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


async def test_get_account_round_trip(client) -> None:
    account_id = await create_account(client, initial_balance="42.00")

    response = await client.get(f"{API}/accounts/{account_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == account_id
    assert body["balance"] == "42.00"


async def test_get_unknown_account_returns_404_envelope(client) -> None:
    response = await client.get(f"{API}/accounts/{uuid.uuid4()}")

    assert response.status_code == 404
    assert error_code(response) == "account_not_found"
    assert "was not found" in response.json()["error"]["message"]


async def test_get_account_with_invalid_uuid_returns_422(client) -> None:
    response = await client.get(f"{API}/accounts/not-a-uuid")

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


async def test_balance_is_never_negative_on_create(client) -> None:
    response = await client.post(
        f"{API}/accounts", json={"owner_name": "Alice", "initial_balance": "-1.00"}
    )

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


async def test_two_accounts_are_independent(client) -> None:
    alice = await create_account(client, owner_name="Alice", initial_balance="10.00")
    bob = await create_account(client, owner_name="Bob", initial_balance="20.00")

    assert await get_balance(client, alice) == "10.00"
    assert await get_balance(client, bob) == "20.00"
