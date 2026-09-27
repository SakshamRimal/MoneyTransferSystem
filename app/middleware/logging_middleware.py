"""
HTTP-level logging middleware.

Assigns every incoming request a correlation id (reusing an inbound
`X-Request-ID` header if the caller/load-balancer already set one, so traces
stay joined across services), stores it in a contextvar for the duration of
the request (picked up automatically by every log record -- see
`app.core.logging.CorrelationIdFilter`), and logs a single structured line
per request with method, path, status code and duration.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import correlation_id_ctx_var, get_logger

logger = get_logger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get(REQUEST_ID_HEADER, str(uuid.uuid4()))
        token = correlation_id_ctx_var.set(correlation_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request_failed",
                extra={
                    "http_method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "request_completed",
                extra={
                    "http_method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            response.headers[REQUEST_ID_HEADER] = correlation_id
            return response
        finally:
            correlation_id_ctx_var.reset(token)
