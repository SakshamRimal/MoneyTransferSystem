from fastapi import APIRouter

from app.api.v1.accounts import router as accounts_router
from app.api.v1.transfers import router as transfers_router

api_router = APIRouter()
api_router.include_router(accounts_router)
api_router.include_router(transfers_router)
