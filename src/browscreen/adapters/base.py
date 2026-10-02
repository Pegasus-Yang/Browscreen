"""公共浏览器契约，不包含具体协议字段。"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class BrowserAdapterError(RuntimeError):
    """连接、页面或截图操作失败的统一错误。"""


class BrowserScreenshot(BaseModel):
    """与 CSS 视口对应的原始 PNG。

    图片有效性由公共合成流程解码验证。
    """

    model_config = ConfigDict(frozen=True)
    png_bytes: bytes = Field(min_length=1)
    css_viewport_width: FiniteFloat = Field(gt=0)
    css_viewport_height: FiniteFloat = Field(gt=0)


class BrowserAdapter(Protocol):
    """外部浏览器的连接、视口采集和自身资源释放契约。"""

    endpoint_file_name: str

    async def connect(self, *, endpoint: str, timeout_s: float) -> None:
        """在总预算内连接并选择已有可截图页面。"""
        ...

    async def capture_viewport(self, *, timeout_s: float) -> BrowserScreenshot:
        """在总预算内返回未合成指针的当前视口。"""
        ...

    async def disconnect(self) -> None:
        """释放自身资源；不关闭外部浏览器或页面。"""
        ...
