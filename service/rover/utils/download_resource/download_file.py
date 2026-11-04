"""资源文件下载。"""

from pathlib import Path
from typing import TYPE_CHECKING, Union

import aiofiles
import httpx

from rover.logger import logger

if TYPE_CHECKING:
    from aiohttp.client import ClientSession


def _download_target(path: Path, name: str) -> Path:
    # Names have already been URL-decoded by the index consumer. Treat them as
    # one portable filename, never as a path or Windows alternate data stream.
    if (type(name) is not str or not name or name in ('.', '..')
            or name.endswith((' ', '.'))
            or any(c in name for c in '/\\:<>"|?*')
            or any(ord(c) < 32 for c in name)
            or name.split('.')[0].upper() in {
                'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),
                *(f'LPT{i}' for i in range(1,10))}):
        raise ValueError('unsafe resource filename')
    root = Path(path).resolve(strict=True)
    target = root / name
    if not root.is_dir() or target.resolve().parent != root or target.is_symlink():
        raise ValueError('resource target escaped directory')
    return target


async def download(
    url: str,
    path: Path,
    name: str,
    sess: Union["ClientSession", httpx.AsyncClient, None] = None,
    tag: str = "",
):
    target = _download_target(path, name)
    logger.info(f"{tag} 开始下载: {name}")
    logger.info(f"{tag} 下载地址: {url}")
    if sess is None:
        sess = httpx.AsyncClient()

    try:
        if isinstance(sess, httpx.AsyncClient):
            res = await sess.get(url)
            content = res.read()
            retcode = res.status_code
        else:
            async with sess.get(url) as resp:
                content = await resp.read()
                retcode = resp.status

        if retcode == 200:
            if _download_target(path, name) != target:
                raise ValueError('resource target changed')
            async with aiofiles.open(target, "wb") as f:
                await f.write(content)
            logger.success(f"{tag} 下载完成: {name}")
        else:
            logger.warning(f"{tag} 下载失败: {name}, 状态码 {retcode}")
        return retcode
    except Exception as e:
        logger.error(e)
        logger.warning(f"{tag} 下载出错: {name}")
