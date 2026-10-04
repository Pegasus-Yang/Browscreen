"""逐帧并发发送，无图片队列和历史回放。"""

import asyncio
import logging

import httpx

from browscreen.models import CurrentFrame

logger = logging.getLogger(__name__)
WEBHOOK_TIMEOUT_S = 3


async def push_frame(*, client: httpx.AsyncClient, urls: tuple[str, ...], frame: CurrentFrame, failed_urls: set[str]) -> None:
    """向本帧地址快照各尝试发送一次，等待全部尝试结束。

    :param frame: 与图片接口共用的完整 PNG 帧。
    :param failed_urls: 跨帧保留的失败地址，仅在首次失败和恢复时输出常规日志。
    """
    async def send(*, url: str) -> None:
        try:
            async with asyncio.timeout(delay=WEBHOOK_TIMEOUT_S):
                response = await client.post(url=url, content=frame.png_bytes, headers={
                    "Content-Type": "image/png", **frame.headers,
                }, timeout=WEBHOOK_TIMEOUT_S, follow_redirects=False)
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as error:
            log = logger.debug if url in failed_urls else logger.warning
            log(msg=f"webhook 发送失败，帧 {frame.frame_id}，地址 {url}：{error!r}")
            failed_urls.add(url)
        else:
            if url in failed_urls:
                failed_urls.remove(url)
                logger.info(msg=f"webhook 发送已恢复，地址 {url}")

    await asyncio.gather(*(send(url=url) for url in urls))
