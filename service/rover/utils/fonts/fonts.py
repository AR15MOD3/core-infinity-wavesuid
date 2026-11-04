"""内置字体。优先用系统中文字体, 缺失时退回 PIL 默认字体。"""

from functools import lru_cache
import os
from pathlib import Path
from typing import Optional

from PIL import ImageFont

FONT_PATH = Path(__file__).parent

_CANDIDATES = (
    FONT_PATH / "rover_font.ttf",
    Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc',
    Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/simsun.ttc',
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/arphic/gbsn00lp.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def _font_file() -> Optional[Path]:
    for path in _CANDIDATES:
        if path.is_file():
            return path
    return None


def core_font(size: int) -> ImageFont.FreeTypeFont:
    path = _font_file()
    if path is None:
        return ImageFont.load_default(size)  # type: ignore[return-value]
    stat = path.stat()
    return _font_at(str(path), size, (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))


@lru_cache(maxsize=64)
def _font_at(path, size, revision):
    return ImageFont.truetype(path, size)
