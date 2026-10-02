"""工作目录文件的读取与退出清理。"""

import logging
from pathlib import Path

from pydantic import ValidationError

from browscreen.models import MousePosition

logger = logging.getLogger(__name__)


def read_endpoint(*, path: Path) -> str | None:
    """读取固定端点文件中的单行文本。

    :returns: 去除 BOM 与空白的文本；不可读、空值或多行时返回 None。
    """
    try:
        content = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        return None
    if not content or len(content.splitlines()) != 1:
        return None
    return content


def read_mouse(*, path: Path) -> MousePosition | None:
    """读取本轮坐标，不保留上一轮的有效值。

    :returns: 有限数值坐标；不可读或格式无效时返回 None。
    """
    try:
        parts = path.read_text(encoding="utf-8-sig").strip().split(",")
        if len(parts) != 2:
            return None
        return MousePosition(x=parts[0].strip(), y=parts[1].strip())
    except (OSError, UnicodeError, ValidationError):
        return None


def clear_mouse(*, path: Path) -> None:
    """退出时截断已有坐标文件，缺失时不创建。

    清理失败记录路径与原因，避免影响其他退出清理。
    """
    try:
        with path.open(mode="r+b") as stream:
            stream.truncate(0)
    except FileNotFoundError:
        return
    except OSError:
        logger.exception(msg=f"无法清空鼠标坐标文件：{path}")
    else:
        logger.info(msg=f"已清空鼠标坐标文件：{path}")
