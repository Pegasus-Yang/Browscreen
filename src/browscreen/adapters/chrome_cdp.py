"""通过原生 CDP 连接已有 Chrome 页面。"""

import asyncio
import base64
import json
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from browscreen.adapters.base import BrowserAdapterError, BrowserScreenshot


def validate_endpoint(*, endpoint: str) -> str:
    """校验 HTTP 调试根入口或 Chrome WebSocket 地址。

    :returns: 地址类型，http、browser 或 page。
    :raises BrowserAdapterError: 地址不符合首版协议。
    """
    try:
        parsed = urlsplit(url=endpoint)
        if not parsed.hostname or any(character.isspace() for character in endpoint):
            raise ValueError("地址缺少主机或包含空白")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("端口无效")
        if parsed.scheme in {"http", "https"} and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment:
            return "http"
        if parsed.scheme in {"ws", "wss"} and not parsed.fragment:
            for kind in ("browser", "page"):
                prefix = f"/devtools/{kind}/"
                if parsed.path.startswith(prefix) and parsed.path[len(prefix):]:
                    return kind
        raise ValueError("需要 HTTP(S) 调试根地址或浏览器／页面 WebSocket 地址")
    except ValueError as error:
        raise BrowserAdapterError(f"无效 Chrome 端点：{error}") from error


class ChromeCdpAdapter:
    """封装 Chrome 端点发现、会话与顺序命令响应。

    :param client: 应用生命周期拥有的共享 HTTP 客户端。
    """

    endpoint_file_name = ".cdp"

    def __init__(self, *, client: httpx.AsyncClient) -> None:
        self.client = client
        self.websocket: ClientConnection | None = None
        self.session_id: str | None = None
        self.command_id = 0

    async def connect(self, *, endpoint: str, timeout_s: float) -> None:
        """在一个总预算内发现端点、连接并附着首个已有页面。"""
        try:
            async with asyncio.timeout(delay=timeout_s):
                kind = validate_endpoint(endpoint=endpoint)
                if kind == "http":
                    response = await self.client.get(url=f"{endpoint.rstrip('/')}/json/version", timeout=timeout_s)
                    response.raise_for_status()
                    endpoint = response.json()["webSocketDebuggerUrl"]
                    kind = validate_endpoint(endpoint=endpoint)
                    if kind == "http":
                        raise BrowserAdapterError("发现结果不是 WebSocket 地址")
                self.websocket = await connect(uri=endpoint, open_timeout=timeout_s, close_timeout=1, max_size=None, proxy=None)
                if kind == "browser":
                    targets = await self._command(method="Target.getTargets")
                    target = next((item for item in targets["targetInfos"] if item.get("type") == "page"), None)
                    if target is None:
                        raise BrowserAdapterError("浏览器没有已有页面")
                    attached = await self._command(method="Target.attachToTarget", params={"targetId": target["targetId"], "flatten": True})
                    self.session_id = attached["sessionId"]
        except (httpx.HTTPError, httpx.InvalidURL, WebSocketException, OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
            raise BrowserAdapterError(f"Chrome 连接失败：{error}") from error

    async def _command(self, *, method: str, params: dict | None = None) -> dict:
        """顺序发送命令并过滤事件、其他编号与其他会话响应。"""
        if self.websocket is None:
            raise BrowserAdapterError("Chrome 尚未连接")
        self.command_id += 1
        command = {"id": self.command_id, "method": method, "params": params or {}}
        if self.session_id is not None:
            command["sessionId"] = self.session_id
        await self.websocket.send(message=json.dumps(obj=command))
        while True:
            message = json.loads(s=await self.websocket.recv())
            if not isinstance(message, dict):
                raise BrowserAdapterError("CDP 响应不是对象")
            if message.get("method") == "Target.detachedFromTarget" and message.get("params", {}).get("sessionId") == self.session_id:
                raise BrowserAdapterError("Chrome 页面调试会话已断开")
            if message.get("method") == "Inspector.detached" and message.get("sessionId") == self.session_id:
                raise BrowserAdapterError("Chrome 页面已断开")
            if message.get("id") != self.command_id:
                continue
            if message.get("sessionId") != self.session_id and not ("error" in message and "sessionId" not in message):
                continue
            if "error" in message:
                raise BrowserAdapterError(f"CDP {method} 失败：{message['error']}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise BrowserAdapterError("CDP 结果不是对象")
            return result

    async def capture_viewport(self, *, timeout_s: float) -> BrowserScreenshot:
        """在一个总预算内测量 CSS 视口并抓取当前 PNG。"""
        try:
            async with asyncio.timeout(delay=timeout_s):
                metrics = await self._command(method="Runtime.evaluate", params={
                    "expression": "({width: window.innerWidth, height: window.innerHeight})",
                    "returnByValue": True,
                })
                viewport = metrics["result"]["value"]
                screenshot = await self._command(method="Page.captureScreenshot", params={
                    "format": "png", "fromSurface": True, "captureBeyondViewport": False,
                })
                return BrowserScreenshot(
                    png_bytes=base64.b64decode(s=screenshot["data"], validate=True),
                    css_viewport_width=viewport["width"],
                    css_viewport_height=viewport["height"],
                )
        except (WebSocketException, OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
            raise BrowserAdapterError(f"Chrome 截图失败：{error}") from error

    async def disconnect(self) -> None:
        """关闭自身 WebSocket，保留浏览器和已有页面。"""
        websocket, self.websocket = self.websocket, None
        self.session_id = None
        if websocket is not None:
            try:
                await websocket.close()
            except (WebSocketException, OSError) as error:
                raise BrowserAdapterError(f"Chrome 连接释放失败：{error}") from error
