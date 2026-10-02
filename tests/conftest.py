"""固定图像与仅用于测试的通用浏览器适配器。"""

import asyncio
from io import BytesIO

import pytest
from PIL import Image

from browscreen.adapters.base import BrowserAdapterError, BrowserScreenshot


def make_png(*, width=128, height=96, color=(42, 80, 120)) -> bytes:
    stream = BytesIO()
    Image.new(mode="RGB", size=(width, height), color=color).save(fp=stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def png_bytes():
    return make_png()


class FakeBrowserAdapter:
    """通过另一端点文件驱动公共流程，无任何 CDP 知识。"""

    endpoint_file_name = ".browser"

    def __init__(self, *, png_bytes: bytes, width=128, height=96):
        self.screenshot = BrowserScreenshot(png_bytes=png_bytes, css_viewport_width=width, css_viewport_height=height)
        self.connections = []
        self.budgets = []
        self.disconnects = 0
        self.captures = 0
        self.fail_connect = False
        self.fail_capture = False
        self.delay = 0

    async def connect(self, *, endpoint, timeout_s):
        self.connections.append(endpoint)
        self.budgets.append(timeout_s)
        if self.fail_connect:
            raise BrowserAdapterError("模拟连接失败")

    async def capture_viewport(self, *, timeout_s):
        self.captures += 1
        if self.delay:
            await asyncio.sleep(delay=self.delay)
        if self.fail_capture:
            self.fail_capture = False
            raise BrowserAdapterError("模拟页面失效")
        return self.screenshot

    async def disconnect(self):
        self.disconnects += 1


async def wait_until(predicate, *, timeout_s=1):
    async with asyncio.timeout(delay=timeout_s):
        while not predicate():
            await asyncio.sleep(delay=0.002)


@pytest.fixture
def fake_adapter(png_bytes):
    return FakeBrowserAdapter(png_bytes=png_bytes)


@pytest.fixture
def wait_for():
    return wait_until


@pytest.fixture
def png_factory():
    return make_png
