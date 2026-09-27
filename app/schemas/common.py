"""Shared response schemas used across resources."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable error code, e.g. 'insufficient_funds'.")
    message: str = Field(..., description="Human-readable explanation of the error.")


class ErrorResponse(BaseModel):
    """Consistent error envelope returned for every 4xx/5xx response."""

    error: ErrorDetail


class PageMeta(BaseModel):
    total: int = Field(..., description="Total number of items matching the query.")
    limit: int
    offset: int
    has_more: bool


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta
