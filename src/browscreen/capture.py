"""公共连接等待、单循环采集和最新帧发布。"""

import asyncio
import logging
from asyncio import sleep
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING

import httpx

from browscreen.adapters.base import BrowserAdapter, BrowserAdapterError
from browscreen.files import read_endpoint, read_mouse
from browscreen.imaging import compose_screenshot
from browscreen.models import CurrentFrame, ErrorResponse, Settings
from browscreen.webhooks import push_frame

if TYPE_CHECKING:
    from browscreen.recording import VideoRecorder

logger = logging.getLogger(__name__)
OPERATION_TIMEOUT_S = 5


class CaptureService:
    """驱动任意符合契约的浏览器适配器。

    :param settings: 实例配置。
    :param adapter: 已由应用选定的适配器。
    :param client: 应用拥有的共享 HTTP 客户端。
    :param recorder: 开启录制时注入的录制器；None 表示沿用基础采集。
    """

    def __init__(self, *, settings: Settings, adapter: BrowserAdapter, client: httpx.AsyncClient,
                 recorder: "VideoRecorder | None" = None) -> None:
        self.settings = settings
        self.adapter = adapter
        self.client = client
        self.recorder = recorder
        self.state = "waiting"
        self.current_frame: CurrentFrame | None = None
        self.frame_id = 0
        self.webhooks: set[str] = set()
        self.failed_webhooks: set[str] = set()

    def unavailable_error(self) -> ErrorResponse:
        """生成当前无图片状态的公共响应。"""
        if self.state == "failed":
            return ErrorResponse(code="capture_failed", message="采集任务异常停止，请检查服务日志后重启")
        if self.state == "timed_out":
            return ErrorResponse(code="browser_wait_timeout", message="等待浏览器连接超时，请修正端点后重启服务")
        if self.state == "connected":
            return ErrorResponse(code="screenshot_not_ready", message="浏览器已连接，正在生成首帧")
        return ErrorResponse(code="waiting_for_browser", message="正在等待浏览器端点可用")

    async def disconnect(self, *, timeout_s: float = 1) -> None:
        """限时释放自身连接，清理失败记录日志。"""
        try:
            async with asyncio.timeout(delay=timeout_s):
                await self.adapter.disconnect()
        except Exception:
            logger.exception("浏览器适配器连接清理失败")

    async def _wait_for_browser(self, *, deadline: float) -> bool:
        """沿用本轮恢复截止时间，每次重试重新读文件。"""
        self.state = "waiting"
        path = self.settings.work_dir / self.adapter.endpoint_file_name
        logger.debug(msg=f"等待浏览器端点：{path}，本轮剩余 {max(0, deadline - monotonic()):.3f} 秒")
        while (remaining := deadline - monotonic()) > 0:
            try:
                async with asyncio.timeout(delay=remaining):
                    endpoint = await asyncio.to_thread(read_endpoint, path=path)
                remaining = deadline - monotonic()
                if endpoint and remaining > 0:
                    budget = min(OPERATION_TIMEOUT_S, remaining)
                    async with asyncio.timeout(delay=budget):
                        await self.adapter.connect(endpoint=endpoint, timeout_s=budget)
                    self.state = "connected"
                    logger.debug("浏览器已连接")
                    return True
            except (BrowserAdapterError, TimeoutError) as error:
                logger.debug(msg=f"浏览器暂不可用：{error!r}")
                await self.disconnect(timeout_s=max(0, min(1, deadline - monotonic())))
            await sleep(delay=min(self.settings.interval_ms / 1000, max(0, deadline - monotonic())))
        self.state = "timed_out"
        logger.warning("等待浏览器连接超时，停止采集与端点轮询")
        return False

    async def run(self) -> None:
        """顺序采集、发布和发送；失效时清空缓存并重新等待。"""
        deadline = monotonic() + self.settings.connect_wait_timeout_s
        logger.info(msg=f"开始等待浏览器画面，等待上限 {self.settings.connect_wait_timeout_s:g} 秒")
        while await self._wait_for_browser(deadline=deadline):
            try:
                while True:
                    started = monotonic()
                    timestamp = datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    # 首个有效帧产出前，连接、文件读取和图片合成共用恢复预算。
                    remaining = None if deadline is None else max(0, deadline - monotonic())
                    async with asyncio.timeout(delay=remaining):
                        mouse = await asyncio.to_thread(read_mouse, path=self.settings.work_dir / ".mouse")
                        budget = OPERATION_TIMEOUT_S if deadline is None else max(0, min(OPERATION_TIMEOUT_S, deadline - monotonic()))
                        async with asyncio.timeout(delay=budget):
                            screenshot = await self.adapter.capture_viewport(timeout_s=budget)
                        png = await asyncio.to_thread(compose_screenshot, screenshot=screenshot, mouse=mouse)
                    if deadline is not None:
                        logger.info("浏览器画面已就绪" if self.frame_id == 0 else "浏览器画面已恢复")
                    deadline = None
                    self.frame_id += 1
                    frame = CurrentFrame(frame_id=self.frame_id, capture_started_at=timestamp, png_bytes=png)
                    urls = tuple(self.webhooks)
                    self.current_frame = frame
                    if self.recorder is not None:
                        await self.recorder.append(frame=frame, captured_at=started)
                    await push_frame(client=self.client, urls=urls, frame=frame, failed_urls=self.failed_webhooks)
                    await sleep(delay=max(0, self.settings.interval_ms / 1000 - (monotonic() - started)))
            except (BrowserAdapterError, TimeoutError) as error:
                self.current_frame = None
                self.state = "waiting"
                if deadline is None:
                    logger.warning(msg=f"浏览器采集失效，开始恢复：{error!r}")
                    deadline = monotonic() + self.settings.connect_wait_timeout_s
                logger.debug(msg=f"浏览器采集失败：{error!r}")
                await self.disconnect(timeout_s=max(0, min(1, deadline - monotonic())))
                await sleep(delay=min(self.settings.interval_ms / 1000, max(0, deadline - monotonic())))
