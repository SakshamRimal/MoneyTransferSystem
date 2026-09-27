"""
Dependency providers.

This module is the "composition root" for the request-scoped object graph:
FastAPI resolves `get_db_session` once per request and everything else here
is built on top of that same session, so a repository, its service, and the
controller all share one transaction for the lifetime of the request.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.repositories.account_repository import AccountRepository
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.transfer_repository import TransferRepository
from app.services.account_service import AccountService
from app.services.transfer_service import TransferService

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_account_repository(session: DbSession) -> AccountRepository:
    return AccountRepository(session)


def get_transfer_repository(session: DbSession) -> TransferRepository:
    return TransferRepository(session)


def get_idempotency_repository(session: DbSession) -> IdempotencyRepository:
    return IdempotencyRepository(session)


AccountRepoDep = Annotated[AccountRepository, Depends(get_account_repository)]
TransferRepoDep = Annotated[TransferRepository, Depends(get_transfer_repository)]
IdempotencyRepoDep = Annotated[IdempotencyRepository, Depends(get_idempotency_repository)]


def get_account_service(session: DbSession, account_repo: AccountRepoDep) -> AccountService:
    return AccountService(session, account_repo)


def get_transfer_service(
    session: DbSession,
    account_repo: AccountRepoDep,
    transfer_repo: TransferRepoDep,
    idempotency_repo: IdempotencyRepoDep,
) -> TransferService:
    return TransferService(session, account_repo, transfer_repo, idempotency_repo)


AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]
TransferServiceDep = Annotated[TransferService, Depends(get_transfer_service)]
