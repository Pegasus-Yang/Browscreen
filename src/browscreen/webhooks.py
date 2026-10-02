"""逐帧并发发送，无图片队列和历史回放。"""

import asyncio
import logging

import httpx

from browscreen.models import CurrentFrame

logger = logging.getLogger(__name__)
WEBHOOK_TIMEOUT_S = 3


async def push_frame(*, client: httpx.AsyncClient, urls: tuple[str, ...], frame: CurrentFrame) -> None:
    """向本帧地址快照各尝试发送一次，等待全部尝试结束。

    :param frame: 与图片接口共用的完整 PNG 帧。
    """
    async def send(*, url: str) -> None:
        try:
            async with asyncio.timeout(delay=WEBHOOK_TIMEOUT_S):
                response = await client.post(url=url, content=frame.png_bytes, headers={
                    "Content-Type": "image/png", **frame.headers,
                }, timeout=WEBHOOK_TIMEOUT_S, follow_redirects=False)
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as error:
            logger.warning(msg=f"webhook 发送失败，帧 {frame.frame_id}，地址 {url}：{error!r}")

    await asyncio.gather(*(send(url=url) for url in urls))
