import logging
import typing
import json
from http import HTTPStatus
from hashlib import sha256
from uuid import uuid4
from typing import Any, List


from aioredis import create_redis_pool, RedisConnection
from itsdangerous.exc import BadTimeSignature, SignatureExpired
from fastapi import Depends, FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from flaskapi_session import (
    REDIS_BACKEND_TYPE,
    SessionManager,
    CookieManager,
    CookieSessionMiddleware,
)

from .settings import Settings


async def session_id_generator(app: FastAPI) -> str:
    """A delegate for generating of a session id."""
    settings: Settings = app.state.settings
    return sha256(f"{settings.SECRET_KEY}:{uuid4()}".encode("utf-8")).hexdigest()


async def session_id_loader(cookie: object) -> Any:
    """A delegate for restoring of a session id from the passed cookie."""
    return cookie


async def backend_adapter_loader(app: FastAPI) -> RedisConnection:
    return app.state.redis


def invalid_cookie_callback(
    request: Request, exc: typing.Union[BadTimeSignature, SignatureExpired]
) -> Response:
    response = Response(status_code=HTTPStatus.BAD_REQUEST)
    response.delete_cookie(settings.SESSION_COOKIE)
    return response


logger = logging.getLogger(__name__)

app = FastAPI()
settings = Settings()
cookie = CookieManager(
    secret_key=settings.SECRET_KEY,
    session_cookie=settings.SESSION_COOKIE,
)
session = SessionManager(
    secret_key=settings.SECRET_KEY,
    backend_type=REDIS_BACKEND_TYPE,
    session_id_loader=session_id_loader,
    backend_adapter_loader=backend_adapter_loader,
)
app.state.settings = settings

# Connect a session middleware to the FastAPI app
app.add_middleware(
    CookieSessionMiddleware,
    cookie_manager=cookie,
    session_manager=session,
    on_invalid_cookie=invalid_cookie_callback,
)


@app.on_event("startup")
async def open_session():
    conn_pool = await create_redis_pool(settings.REDIS_DSN)
    app.state.redis = conn_pool


@app.on_event("shutdown")
async def close_session():
    conn_pool = app.state.redis
    await conn_pool
    await conn_pool.wait_closed()


@app.post("/init/")
async def init_session(response: Response) -> Response:
    response = cookie.set_cookie(response, await session_id_generator(app))
    response.status_code = HTTPStatus.OK
    return response


@app.post("/set/{key}/{value}/")
async def add_to_session(
    request: Request, response: Response, key: str, value: Any
) -> Response:
    """Add the value to the session by the key"""
    if "session" not in request:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST)
    result = await request.session.set(key, value)
    response.status_code = HTTPStatus.OK
    return response


@app.post("/get/{key}/")
async def get_from_session(request: Request, response: Response, key: str) -> Response:
    """Get a session value by the key"""
    if "session" not in request:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST)
    value = list(await request.session.get(key)).pop()
    return JSONResponse(content={key: value}, status_code=HTTPStatus.OK)


@app.post("/flush/")
async def flush_session(request: Request, response: Response) -> Response:
    if "session" not in request:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST)
    await request.session.clear()
    response.status_code = HTTPStatus.OK
    return response


@app.post("/close/")
async def close_session(request: Request, response: Response) -> Response:
    response = cookie.remove_cookie(response)
    response.status_code = HTTPStatus.OK
    return response