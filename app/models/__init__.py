"""
Import all ORM models here so that `Base.metadata` is fully populated
whenever `app.models` is imported -- this is what Alembic's `env.py` relies
on for autogeneration to see every table.
"""

from app.models.account import Account, AccountStatus  # noqa: F401
from app.models.idempotency import IdempotencyKey  # noqa: F401
from app.models.transfer import (  # noqa: F401
    EntryDirection,
    LedgerEntry,
    Transfer,
    TransferStatus,
)

__all__ = [
    "Account",
    "AccountStatus",
    "Transfer",
    "TransferStatus",
    "LedgerEntry",
    "EntryDirection",
    "IdempotencyKey",
]
