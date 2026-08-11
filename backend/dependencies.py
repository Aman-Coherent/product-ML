"""Shared FastAPI dependencies: Redis client and ARQ enqueue pool."""
from __future__ import annotations

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from backend.config import get_settings

_redis_client: Redis | None = None
_arq_pool: ArqRedis | None = None


async def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(get_settings().REDIS_URL, decode_responses=False)
    return _redis_client


async def get_arq_pool() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().REDIS_URL))
    return _arq_pool


async def close_connections() -> None:
    global _redis_client, _arq_pool
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
    if _arq_pool is not None:
        await _arq_pool.aclose()
        _arq_pool = None


async def redis_dependency() -> Redis:
    return await get_redis_client()


async def arq_dependency() -> ArqRedis:
    return await get_arq_pool()
