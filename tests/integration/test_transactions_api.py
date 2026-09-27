"""Integration tests for GET /accounts/{id}/transactions (history + pagination)."""

import uuid

from tests.support import API, create_account, error_code, transfer


async def make_transfers(client, alice: str, bob: str, count: int) -> list[str]:
    ids = []
    for index in range(1, count + 1):
        response = await transfer(client, alice, bob, f"{index}.00")
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


async def test_empty_history(client) -> None:
    account_id = await create_account(client)

    response = await client.get(f"{API}/accounts/{account_id}/transactions")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["meta"] == {"total": 0, "limit": 20, "offset": 0, "has_more": False}


async def test_unknown_account_returns_404(client) -> None:
    response = await client.get(f"{API}/accounts/{uuid.uuid4()}/transactions")

    assert response.status_code == 404
    assert error_code(response) == "account_not_found"


async def test_history_is_paginated_newest_first(client) -> None:
    alice = await create_account(client, initial_balance="1000.00")
    bob = await create_account(client)
    transfer_ids = await make_transfers(client, alice, bob, 5)

    page1 = await client.get(f"{API}/accounts/{alice}/transactions?limit=2&offset=0")
    page2 = await client.get(f"{API}/accounts/{alice}/transactions?limit=2&offset=2")
    page3 = await client.get(f"{API}/accounts/{alice}/transactions?limit=2&offset=4")

    assert page1.status_code == page2.status_code == page3.status_code == 200

    body1, body2, body3 = page1.json(), page2.json(), page3.json()
    assert body1["meta"] == {"total": 5, "limit": 2, "offset": 0, "has_more": True}
    assert body2["meta"] == {"total": 5, "limit": 2, "offset": 2, "has_more": True}
    assert body3["meta"] == {"total": 5, "limit": 2, "offset": 4, "has_more": False}

    # Newest first: the last transfer we made appears on page 1.
    seen = [item["transfer_id"] for page in (body1, body2, body3) for item in page["items"]]
    assert seen == list(reversed(transfer_ids))


async def test_balance_after_runs_across_pages(client) -> None:
    alice = await create_account(client, initial_balance="100.00")
    bob = await create_account(client)
    await make_transfers(client, alice, bob, 3)  # -1, -2, -3

    response = await client.get(f"{API}/accounts/{alice}/transactions?limit=10")

    balances = [item["balance_after"] for item in response.json()["items"]]
    assert balances == ["94.00", "97.00", "99.00"]  # newest first


async def test_counterparty_sees_credit_side(client) -> None:
    alice = await create_account(client, initial_balance="50.00")
    bob = await create_account(client)
    await make_transfers(client, alice, bob, 1)

    response = await client.get(f"{API}/accounts/{bob}/transactions")

    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["direction"] == "credit"
    assert items[0]["balance_after"] == "1.00"
    assert items[0]["counterparty_account_id"] == alice


async def test_limit_bounds_are_validated(client) -> None:
    account_id = await create_account(client)

    too_big = await client.get(f"{API}/accounts/{account_id}/transactions?limit=101")
    zero = await client.get(f"{API}/accounts/{account_id}/transactions?limit=0")
    negative_offset = await client.get(f"{API}/accounts/{account_id}/transactions?offset=-1")

    assert too_big.status_code == 422
    assert zero.status_code == 422
    assert negative_offset.status_code == 422
    assert error_code(too_big) == "validation_error"


async def test_offset_beyond_total_returns_empty_page(client) -> None:
    alice = await create_account(client, initial_balance="100.00")
    bob = await create_account(client)
    await make_transfers(client, alice, bob, 2)

    response = await client.get(f"{API}/accounts/{alice}/transactions?offset=50")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["meta"]["total"] == 2
    assert response.json()["meta"]["has_more"] is False
