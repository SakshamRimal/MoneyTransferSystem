"""Integration tests for Idempotency-Key semantics on POST /transfers."""

import uuid

from tests.support import API, create_account, error_code, get_balance, transfer


async def test_replay_returns_original_result_and_moves_money_once(client) -> None:
    alice = await create_account(client, initial_balance="100.00")
    bob = await create_account(client, initial_balance="0.00")
    key = str(uuid.uuid4())

    first = await transfer(client, alice, bob, "25.00", idempotency_key=key)
    second = await transfer(client, alice, bob, "25.00", idempotency_key=key)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert second.json() == first.json()
    assert await get_balance(client, alice) == "75.00"
    assert await get_balance(client, bob) == "25.00"

    history = await client.get(f"{API}/accounts/{alice}/transactions")
    assert history.json()["meta"]["total"] == 1  # only one transfer executed


async def test_replay_of_many_requests_still_moves_money_once(client) -> None:
    alice = await create_account(client, initial_balance="100.00")
    bob = await create_account(client, initial_balance="0.00")
    key = str(uuid.uuid4())

    responses = [await transfer(client, alice, bob, "10.00", idempotency_key=key) for _ in range(5)]

    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert await get_balance(client, alice) == "90.00"


async def test_same_key_with_different_payload_conflicts(client) -> None:
    alice = await create_account(client, initial_balance="100.00")
    bob = await create_account(client, initial_balance="0.00")
    key = str(uuid.uuid4())

    first = await transfer(client, alice, bob, "10.00", idempotency_key=key)
    conflicting = await transfer(client, alice, bob, "20.00", idempotency_key=key)

    assert first.status_code == 201
    assert conflicting.status_code == 409
    assert error_code(conflicting) == "idempotency_key_conflict"
    assert await get_balance(client, alice) == "90.00"  # only the first executed


async def test_different_keys_execute_independently(client) -> None:
    alice = await create_account(client, initial_balance="100.00")
    bob = await create_account(client, initial_balance="0.00")

    first = await transfer(client, alice, bob, "10.00", idempotency_key=str(uuid.uuid4()))
    second = await transfer(client, alice, bob, "10.00", idempotency_key=str(uuid.uuid4()))

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert await get_balance(client, alice) == "80.00"


async def test_without_key_every_request_executes(client) -> None:
    """Documented trade-off: no key, no dedup (at-least-once is client's call)."""
    alice = await create_account(client, initial_balance="100.00")
    bob = await create_account(client, initial_balance="0.00")

    first = await transfer(client, alice, bob, "10.00")
    second = await transfer(client, alice, bob, "10.00")

    assert first.status_code == 201
    assert second.status_code == 201
    assert await get_balance(client, alice) == "80.00"


async def test_failed_transfer_does_not_burn_the_key(client) -> None:
    """A rejected first attempt must not make later retries return a cached 4xx."""
    alice = await create_account(client, initial_balance="10.00")
    bob = await create_account(client, initial_balance="0.00")
    carol = await create_account(client, initial_balance="100.00")
    key = str(uuid.uuid4())

    first = await transfer(client, alice, bob, "50.00", idempotency_key=key)
    assert first.status_code == 422
    assert error_code(first) == "insufficient_funds"

    # Top Alice up, then retry the *identical* request with the same key:
    # it must execute for real instead of replaying the earlier rejection.
    assert (await transfer(client, carol, alice, "50.00")).status_code == 201
    retry = await transfer(client, alice, bob, "50.00", idempotency_key=key)

    assert retry.status_code == 201
    assert await get_balance(client, alice) == "10.00"  # 60.00 - 50.00
    assert await get_balance(client, bob) == "50.00"
