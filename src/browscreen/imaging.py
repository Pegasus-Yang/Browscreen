"""视口 PNG 校验与透明鼠标指针合成。"""

from functools import cache
from importlib.resources import files
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from browscreen.adapters.base import BrowserAdapterError, BrowserScreenshot
from browscreen.models import MousePosition

# PNG 像素单位；箭头尖端位于资源图的 (1, 1)。
CURSOR_HOTSPOT = (1, 1)


@cache
def _cursor_image() -> Image.Image:
    with files(anchor="browscreen").joinpath("assets/cursor.png").open(mode="rb") as stream:
        with Image.open(fp=stream) as image:
            return image.convert(mode="RGBA")


def compose_screenshot(*, screenshot: BrowserScreenshot, mouse: MousePosition | None) -> bytes:
    """验证原始 PNG，按实际 PNG／CSS 比例叠加本轮指针。

    :returns: 当前视口 PNG；无有效视口内坐标时保留原始字节。
    :raises BrowserAdapterError: 原始截图不是可解码的 PNG。
    """
    try:
        with Image.open(fp=BytesIO(initial_bytes=screenshot.png_bytes)) as source:
            if source.format != "PNG":
                raise BrowserAdapterError("适配器截图必须为 PNG")
            source.load()
            if mouse is None or not (0 <= mouse.x < screenshot.css_viewport_width and 0 <= mouse.y < screenshot.css_viewport_height):
                return screenshot.png_bytes
            image = source.convert(mode="RGBA")
        point = (
            round(mouse.x * image.width / screenshot.css_viewport_width) - CURSOR_HOTSPOT[0],
            round(mouse.y * image.height / screenshot.css_viewport_height) - CURSOR_HOTSPOT[1],
        )
        image.alpha_composite(im=_cursor_image(), dest=point)
        output = BytesIO()
        image.save(fp=output, format="PNG")
        return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise BrowserAdapterError(f"PNG 解码或合成失败：{error}") from error
