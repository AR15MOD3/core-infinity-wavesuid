"""图片网络请求。"""

import asyncio
import hashlib
import os
import time
import tempfile
from pathlib import Path
from typing import Optional

import httpx

from rover.data_store import get_res_path
from rover.logger import logger
from rover.pool import to_thread as owned_thread

_SGET_CONNECT_TIMEOUT: float = 3.0
_SGET_READ_TIMEOUT: float = 8.0
_SGET_WRITE_TIMEOUT: float = 3.0
_SGET_POOL_TIMEOUT: float = 3.0
_SGET_TOTAL_TIMEOUT: float = 12.0
_SGET_CACHE_TTL: float = 172800.0  # 2 天

_URL_CACHE_DIR: Path = get_res_path(["IMAGE_CACHE", "sget"])


def _url_cache_path(url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _URL_CACHE_DIR / f"{key}.bin"


async def _read_cached(url: str) -> Optional[httpx.Response]:
    path = _url_cache_path(url)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > _SGET_CACHE_TTL:
        return None
    content = await owned_thread(path.read_bytes)()
    request = httpx.Request("GET", url)
    return httpx.Response(200, content=content, request=request)


@owned_thread
def _commit_cache(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, made = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    temp = Path(made)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content); output.flush(); os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()

async def _write_cache(url: str, content: bytes) -> None:
    await _commit_cache(_url_cache_path(url), content)


async def sget(url: str, use_cache: bool = False) -> httpx.Response:
    if use_cache:
        cached = await _read_cached(url)
        if cached is not None:
            return cached

    logger.info(f"[图片] 开始下载: url-sha256={hashlib.sha256(url.encode()).hexdigest()[:16]}")
    timeout = httpx.Timeout(
        connect=_SGET_CONNECT_TIMEOUT,
        read=_SGET_READ_TIMEOUT,
        write=_SGET_WRITE_TIMEOUT,
        pool=_SGET_POOL_TIMEOUT,
    )
    async with asyncio.timeout(_SGET_TOTAL_TIMEOUT):
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url=url)
            resp.raise_for_status()
            if use_cache:
                await _write_cache(url, resp.content)
            return resp
