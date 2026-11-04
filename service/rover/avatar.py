"""头像配置解析。

配置项 avatar 支持三种写法: 本地图片路径 / http(s) 链接 / 纯数字 QQ 号,
统一解析成能直接放进 <img src> 的地址; 置空则不覆盖, 沿用插件自己取的头像。
"""

import base64
import os
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from rover.logger import logger

QQ_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={}&s=640"

# 按文件头认图片类型, 不信扩展名
MAGICS = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"BM", "image/bmp"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
)

# Compatibility container only; reads use current bounded bytes, never stale metadata hits.
_file_cache: Dict[str, Tuple[float, int, str]] = {}
# 同一个坏配置只提示一次
_warned: Set[str] = set()
MAX_AVATAR_BYTES = 8 * 1024 * 1024


def _warn(raw: str, reason: str) -> None:
    if raw in _warned:
        return
    if len(_warned) >= 128:
        _warned.clear()
    _warned.add(raw)
    logger.warning("[头像] 配置无效, 回退默认头像")


def _mime(data: bytes) -> Optional[str]:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in MAGICS:
        if data.startswith(magic):
            return mime
    if b"<svg" in data[:512].lower():
        return "image/svg+xml"
    return None


def _data_uri(raw: str) -> str:
    path = Path(raw).expanduser()
    try:
        stat = path.stat()
    except OSError as e:
        _warn(raw, f"读不到文件: {e}")
        return ""
    if stat.st_size > MAX_AVATAR_BYTES:
        _warn(raw, '图片超过本地读取上限')
        return ''
    try:
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(MAX_AVATAR_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.stat()
    except OSError as e:
        _warn(raw, f"读文件失败: {e}")
        return ""
    stamp = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if (len(data) > MAX_AVATAR_BYTES or len(data) != after.st_size
            or stamp(before) != stamp(after) or stamp(after) != stamp(current)):
        _warn(raw, '读取期间文件发生变化')
        return ''
    mime = _mime(data)
    if mime is None:
        _warn(raw, "不是图片文件")
        return ""
    uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    # Read bounded current bytes each time. Same-size/mtime replacements must
    # not reuse another image; avoid retaining unbounded data-URI cache entries.
    _warned.discard(raw)
    return uri


def get_avatar_url() -> str:
    """返回配置的头像地址; 未配置或配置无效时返回空串。"""
    from rover.config import core_config

    raw = str(core_config.get_config("avatar") or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "data:")):
        return raw
    if raw.isascii() and raw.isdigit():
        return QQ_AVATAR_URL.format(raw)
    return _data_uri(raw)
