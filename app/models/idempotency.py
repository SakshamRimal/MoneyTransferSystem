"""
IdempotencyKey model.

Stores a fingerprint of the request body alongside the resulting HTTP
status/body so that a retried request with the same `Idempotency-Key`
header:

  1. If the original request is still being processed (rare race, e.g. two
     nearly-simultaneous retries) -> the unique DB constraint on `key` makes
     the second insert fail fast, and the caller is asked to retry shortly.
  2. If the original request already completed -> the stored response is
     replayed verbatim, without re-running business logic.
  3. If the same key is reused with a *different* request body -> rejected
     with a 409 conflict, since silently accepting it would be dangerous.

Scoped per-endpoint via `endpoint` so the same key value can't accidentally
collide across unrelated operations.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("key", "endpoint", name="uq_idempotency_key_endpoint"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IdempotencyKey key={self.key} endpoint={self.endpoint}>"
