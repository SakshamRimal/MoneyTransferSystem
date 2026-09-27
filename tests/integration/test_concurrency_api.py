"""
Concurrency tests.

These use `committed_client` (real connections, real commits) so that
`SELECT ... FOR UPDATE` and advisory locks actually contend, exactly as they
would under a real server. The transactional `client` fixture would put every
request in one shared transaction, which would silently disable all locking
and make these tests meaningless.

Assertions are exact: with money as integers, either the final balances match
the arithmetic precisely or the code is wrong.
"""

import asyncio
import uuid

from httpx import AsyncClient

from tests.support import create_account, error_code, get_balance, transfer

# Generous upper bound so a deadlock/lock-stall fails the test instead of
# hanging the suite forever.
CONCURRENCY_TIMEOUT_SECONDS = 60


async def run_concurrently(*coroutines) -> list:
    return list(
        await asyncio.wait_for(asyncio.gather(*coroutines), timeout=CONCURRENCY_TIMEOUT_SECONDS)
    )


async def test_concurrent_debits_never_overdraw(committed_client: AsyncClient) -> None:
    """40 simultaneous 10.00 debits against a 100.00 balance.

    Exactly 10 can succeed; the other 30 must be rejected with
    insufficient_funds. A lost-update race would produce a negative balance
    or more money leaving the account than exists.
    """
    source = await create_account(committed_client, initial_balance="100.00")
    sink = await create_account(committed_client, initial_balance="0.00")

    responses = await run_concurrently(
        *(transfer(committed_client, source, sink, "10.00") for _ in range(40))
    )

    succeeded = [r for r in responses if r.status_code == 201]
    rejected = [r for r in responses if r.status_code == 422]

    assert len(succeeded) == 10, f"expected 10 successes, got {len(succeeded)}"
    assert len(rejected) == 30
    assert {error_code(r) for r in rejected} == {"insufficient_funds"}

    assert await get_balance(committed_client, source) == "0.00"
    assert await get_balance(committed_client, sink) == "100.00"

    # The ledger agrees with the balance: 10 debits totalling exactly 100.00.
    history = await committed_client.get(f"/api/v1/accounts/{source}/transactions")
    items = history.json()["items"]
    assert len(items) == 10
    assert sum(float(item["amount"]) for item in items) == 100.00
    assert {item["direction"] for item in items} == {"debit"}


async def test_bidirectional_concurrent_transfers_conserve_money(
    committed_client: AsyncClient,
) -> None:
    """20 opposite-direction transfers between the same pair must not deadlock.

    Locking both accounts in a fixed global order is what makes this safe;
    without it, A->B and B->A could each hold one row lock and wait forever.
    """
    alice = await create_account(committed_client, owner_name="Alice", initial_balance="50.00")
    bob = await create_account(committed_client, owner_name="Bob", initial_balance="50.00")

    responses = await run_concurrently(
        *(transfer(committed_client, alice, bob, "1.00") for _ in range(10)),
        *(transfer(committed_client, bob, alice, "1.00") for _ in range(10)),
    )

    assert [r.status_code for r in responses] == [201] * 20

    assert await get_balance(committed_client, alice) == "50.00"
    assert await get_balance(committed_client, bob) == "50.00"


async def test_concurrent_retries_with_same_key_execute_once(
    committed_client: AsyncClient,
) -> None:
    """10 simultaneous retries carrying one Idempotency-Key move money once.

    The advisory transaction lock serialises them: the first executes, the
    rest block until it commits and then replay the stored response.
    """
    alice = await create_account(committed_client, initial_balance="100.00")
    bob = await create_account(committed_client, initial_balance="0.00")
    key = str(uuid.uuid4())

    responses = await run_concurrently(
        *(transfer(committed_client, alice, bob, "30.00", idempotency_key=key) for _ in range(10))
    )

    assert [r.status_code for r in responses] == [201] * 10
    assert len({r.json()["id"] for r in responses}) == 1

    assert await get_balance(committed_client, alice) == "70.00"
    assert await get_balance(committed_client, bob) == "30.00"

    history = await committed_client.get(f"/api/v1/accounts/{alice}/transactions")
    assert history.json()["meta"]["total"] == 1
