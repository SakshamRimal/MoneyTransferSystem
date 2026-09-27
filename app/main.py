"""
Application entrypoint.

Builds the FastAPI app, registers the router, middleware, and a small set
of exception handlers that translate our domain exceptions (see
`app.core.exceptions`) and validation errors into one consistent error
envelope, per the P1 "consistent error responses" requirement.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.logging import configure_logging, get_logger
from app.middleware.logging_middleware import RequestLoggingMiddleware

settings = get_settings()
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("application_startup", extra={"env": settings.app_env})
    yield
    logger.info("application_shutdown")


app = FastAPI(
    title=settings.app_name,
    description="An API-only money transfer system with atomic transfers, "
    "concurrency-safe balance updates, and idempotent retries.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(RequestLoggingMiddleware)
app.include_router(api_router, prefix=settings.api_v1_prefix)


def _error_envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    logger.warning(
        "domain_error",
        extra={"error_code": exc.code, "path": request.url.path},
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_envelope(exc.code, exc.message),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Flatten pydantic's error list into one human-readable message while
    # keeping the response envelope identical to every other error case.
    first_error = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first_error.get("loc", []) if p != "body")
    message = first_error.get("msg", "Invalid request payload.")
    detail = f"{field}: {message}" if field else message
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_envelope("validation_error", detail),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope("http_error", str(exc.detail)),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_envelope("internal_server_error", "An unexpected error occurred."),
    )


@app.get("/health", tags=["ops"], summary="Liveness/readiness probe")
async def health() -> dict:
    return {"status": "ok"}
