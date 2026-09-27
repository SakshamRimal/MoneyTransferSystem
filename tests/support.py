"""
Small helpers shared by the integration tests.

Kept as plain functions (not fixtures) so they can be bound to either the
transactional `client` or the `committed_client` depending on the test.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

API = "/api/v1"


async def create_account(
    client: AsyncClient,
    *,
    owner_name: str = "Alice",
    currency: str = "USD",
    initial_balance: str = "0.00",
) -> str:
    """Create an account through the API and return its id."""
    response = await client.post(
        f"{API}/accounts",
        json={
            "owner_name": owner_name,
            "currency": currency,
            "initial_balance": initial_balance,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def get_balance(client: AsyncClient, account_id: str) -> str:
    """Fetch an account's balance as a major-unit string, e.g. '42.00'."""
    response = await client.get(f"{API}/accounts/{account_id}")
    assert response.status_code == 200, response.text
    return response.json()["balance"]


async def transfer(
    client: AsyncClient,
    from_id: str,
    to_id: str,
    amount: str,
    *,
    currency: str | None = None,
    idempotency_key: str | None = None,
    description: str | None = None,
) -> Any:
    """POST /transfers, returning the raw httpx Response."""
    payload: dict[str, Any] = {
        "from_account_id": from_id,
        "to_account_id": to_id,
        "amount": amount,
    }
    if currency is not None:
        payload["currency"] = currency
    if description is not None:
        payload["description"] = description
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return await client.post(f"{API}/transfers", json=payload, headers=headers)


def error_code(response: Any) -> str:
    """Extract the machine-readable code from the standard error envelope."""
    return response.json()["error"]["code"]
