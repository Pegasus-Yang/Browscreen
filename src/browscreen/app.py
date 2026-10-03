"""FastAPI 生命周期、只读图片预览和 webhook 注册。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from importlib.resources import files

import httpx
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse

from browscreen.adapters.base import BrowserAdapter
from browscreen.adapters.chrome_cdp import ChromeCdpAdapter
from browscreen.capture import CaptureService
from browscreen.files import clear_mouse
from browscreen.models import ErrorResponse, Settings, WebhookRegistration

logger = logging.getLogger(__name__)
ADAPTER_FACTORIES = {"chrome-cdp": ChromeCdpAdapter}


def create_app(*, settings: Settings, adapter: BrowserAdapter | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    """创建实例；注入的适配器和客户端也由该实例负责释放。

    :param settings: 经校验的启动配置。
    :param adapter: 测试或集成可注入符合公共契约的适配器。
    :param client: 测试可注入 HTTP 客户端；默认直接连接端点和接收器。
    :returns: 含一个后台采集任务的应用。
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        http_client = client if client is not None else httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False)
        browser = adapter if adapter is not None else ADAPTER_FACTORIES[settings.adapter](client=http_client)
        service = CaptureService(settings=settings, adapter=browser, client=http_client)
        app.state.capture = service

        async def capture() -> None:
            """立即报告未预期的任务异常，并停止提供旧图。"""
            try:
                await service.run()
            except Exception:
                service.current_frame = None
                service.state = "failed"
                logger.exception("采集任务异常停止")
                await service.disconnect()

        task = asyncio.create_task(coro=capture(), name="browscreen-capture")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("采集任务退出时报告错误")
            await service.disconnect()
            try:
                await http_client.aclose()
            except Exception:
                logger.exception("HTTP 客户端关闭失败")
            finally:
                await asyncio.to_thread(clear_mouse, path=settings.work_dir / ".mouse")

    app = FastAPI(title="browscreen", version="0.1.0", lifespan=lifespan)
    preview = files(anchor="browscreen").joinpath("preview.html").read_text(encoding="utf-8").replace("__INTERVAL_MS__", str(settings.interval_ms))

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """返回按配置间隔读取同一最新帧的只读页面。"""
        return HTMLResponse(content=preview, headers={"Cache-Control": "no-store"})

    @app.get("/api/screenshot", response_class=Response, responses={200: {"content": {"image/png": {}}}, 503: {"model": ErrorResponse}})
    async def screenshot() -> Response:
        """读取一次完整帧引用，返回 PNG 或明确的不可用状态。"""
        service = app.state.capture
        frame = service.current_frame
        if frame is None:
            return JSONResponse(content=service.unavailable_error().model_dump(), status_code=503, headers={"Cache-Control": "no-store"})
        return Response(content=frame.png_bytes, media_type="image/png", headers={"Cache-Control": "no-store", **frame.headers})

    @app.post("/api/webhooks", status_code=201)
    async def register(registration: WebhookRegistration, response: Response) -> dict[str, str | bool]:
        """规范化去重，只参与注册后的新帧。"""
        url = str(registration.url)
        urls = app.state.capture.webhooks
        response.status_code = 200 if url in urls else 201
        urls.add(url)
        return {"url": url, "registered": True}

    return app
