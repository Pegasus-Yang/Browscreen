"""使用真实本地 WebSocket 服务验证 CDP 适配器。"""

import asyncio
import base64
import json

import httpx
import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from browscreen.adapters.base import BrowserAdapterError
from browscreen.adapters.chrome_cdp import ChromeCdpAdapter, validate_endpoint


@pytest.mark.parametrize("endpoint", ["abc", "ftp://localhost", "http://localhost/path", "http://localhost:99999", "ws://localhost/", "http://bad host", "http://localhost\nhttp://other"])
def test_invalid_endpoints(endpoint):
    with pytest.raises(BrowserAdapterError):
        validate_endpoint(endpoint=endpoint)


@pytest.mark.parametrize("endpoint,kind", [("http://localhost:9222", "http"), ("https://localhost/", "http"), ("ws://localhost/devtools/browser/one", "browser"), ("wss://localhost/devtools/page/one", "page")])
def test_supported_endpoints(endpoint, kind):
    assert validate_endpoint(endpoint=endpoint) == kind


@pytest.mark.parametrize("entry", ["http", "browser", "page"])
async def test_discovery_sessions_matching_and_large_messages(entry, png_bytes):
    commands = []

    async def handler(websocket):
        async for raw in websocket:
            command = json.loads(raw)
            commands.append(command)
            method = command["method"]
            results = {
                "Target.getTargets": {"targetInfos": [{"type": "worker", "targetId": "skip"}, {"type": "page", "targetId": "chosen"}]},
                "Target.attachToTarget": {"sessionId": "session"},
                "Runtime.evaluate": {"result": {"value": {"width": 128, "height": 96}}},
                "Page.captureScreenshot": {"data": base64.b64encode(png_bytes).decode(), "padding": "x" * 1_100_000},
            }
            await websocket.send(json.dumps({"method": "Page.event", "params": {}}))
            await websocket.send(json.dumps({"id": 999, "result": {}}))
            response = {"id": command["id"], "result": results[method]}
            if "sessionId" in command:
                await websocket.send(json.dumps({**response, "sessionId": "unrelated"}))
                response["sessionId"] = command["sessionId"]
            await websocket.send(json.dumps(response))

    async with serve(handler, host="127.0.0.1", port=0) as server:
        port = server.sockets[0].getsockname()[1]
        kind = "page" if entry == "page" else "browser"
        websocket_url = f"ws://127.0.0.1:{port}/devtools/{kind}/one"
        discovery_paths = []

        async def discovery(request):
            discovery_paths.append(request.url.path)
            return httpx.Response(status_code=200, json={"webSocketDebuggerUrl": websocket_url})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler=discovery)) as client:
            adapter = ChromeCdpAdapter(client=client)
            endpoint = "http://127.0.0.1:9222" if entry == "http" else websocket_url
            await adapter.connect(endpoint=endpoint, timeout_s=1)
            screenshot = await adapter.capture_viewport(timeout_s=1)
            assert screenshot.png_bytes == png_bytes
            assert (screenshot.css_viewport_width, screenshot.css_viewport_height) == (128, 96)
            page_commands = [item for item in commands if item["method"] in {"Runtime.evaluate", "Page.captureScreenshot"}]
            assert all(item.get("sessionId") == (None if entry == "page" else "session") for item in page_commands)
            assert page_commands[0]["params"] == {"expression": "({width: window.innerWidth, height: window.innerHeight})", "returnByValue": True}
            assert page_commands[-1]["params"] == {"format": "png", "fromSurface": True, "captureBeyondViewport": False}
            if entry != "page":
                assert commands[1]["params"] == {"targetId": "chosen", "flatten": True}
            assert discovery_paths == (["/json/version"] if entry == "http" else [])
            await adapter.disconnect()
            await adapter.disconnect()
            assert not client.is_closed
            assert not any(item["method"] in {"Browser.close", "Target.closeTarget"} for item in commands)


@pytest.mark.parametrize("failure", ["lost_session_error", "detached_event", "screenshot_error", "invalid_base64", "invalid_dimensions"])
async def test_capture_failure_is_unified_and_detached_session_is_immediate(failure, png_bytes):
    async def handler(websocket):
        async for raw in websocket:
            command = json.loads(raw)
            method = command["method"]
            results = {
                "Target.getTargets": {"targetInfos": [{"type": "page", "targetId": "one"}]},
                "Target.attachToTarget": {"sessionId": "session"},
                "Runtime.evaluate": {"result": {"value": {"width": 0 if failure == "invalid_dimensions" else 128, "height": 96}}},
                "Page.captureScreenshot": {"data": "invalid" if failure == "invalid_base64" else base64.b64encode(png_bytes).decode()},
            }
            response = {"id": command["id"], "result": results[method]}
            if "sessionId" in command:
                response["sessionId"] = command["sessionId"]
            if method == "Page.captureScreenshot":
                if failure == "detached_event":
                    response = {"method": "Target.detachedFromTarget", "params": {"sessionId": "session"}}
                elif failure in {"lost_session_error", "screenshot_error"}:
                    response = {"id": command["id"], "error": {"message": "session unavailable"}}
                    if failure == "screenshot_error":
                        response["sessionId"] = "session"
            await websocket.send(json.dumps(response))

    async with serve(handler, host="127.0.0.1", port=0) as server:
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as client:
            adapter = ChromeCdpAdapter(client=client)
            await adapter.connect(endpoint=f"ws://127.0.0.1:{port}/devtools/browser/one", timeout_s=0.5)
            started = asyncio.get_running_loop().time()
            with pytest.raises(BrowserAdapterError):
                await adapter.capture_viewport(timeout_s=0.5)
            assert asyncio.get_running_loop().time() - started < 0.2
            await adapter.disconnect()


@pytest.mark.parametrize("failure", ["no_page", "protocol_error", "malformed_json", "connection_closed"])
async def test_failures_are_adapter_errors(failure):
    async def handler(websocket):
        command = json.loads(await websocket.recv())
        if failure == "connection_closed":
            await websocket.close()
            return
        if failure == "malformed_json":
            await websocket.send("invalid-json")
            return
        response = {"id": command["id"]}
        if failure == "no_page":
            response["result"] = {"targetInfos": []}
        else:
            response["error"] = {"code": -32000, "message": "failed"}
        await websocket.send(json.dumps(response))

    async with serve(handler, host="127.0.0.1", port=0) as server:
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as client:
            adapter = ChromeCdpAdapter(client=client)
            with pytest.raises(BrowserAdapterError):
                await adapter.connect(endpoint=f"ws://127.0.0.1:{port}/devtools/browser/one", timeout_s=0.2)
            await adapter.disconnect()


async def test_capture_requests_share_one_total_budget(png_bytes):
    commands = []

    async def handler(websocket):
        async for raw in websocket:
            command = json.loads(raw)
            commands.append(command)
            await asyncio.sleep(delay=0.04)
            result = {"result": {"value": {"width": 128, "height": 96}}} if len(commands) == 1 else {"data": base64.b64encode(png_bytes).decode()}
            try:
                await websocket.send(json.dumps({"id": command["id"], "result": result}))
            except ConnectionClosed:
                return

    async with serve(handler, host="127.0.0.1", port=0) as server:
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as client:
            adapter = ChromeCdpAdapter(client=client)
            await adapter.connect(endpoint=f"ws://127.0.0.1:{port}/devtools/page/one", timeout_s=1)
            started = asyncio.get_running_loop().time()
            with pytest.raises(BrowserAdapterError, match="截图失败"):
                await adapter.capture_viewport(timeout_s=0.06)
            assert asyncio.get_running_loop().time() - started < 0.1
            assert len(commands) == 2
            await adapter.disconnect()


async def test_connect_requests_share_one_total_budget():
    methods = []

    async def handler(websocket):
        async for raw in websocket:
            command = json.loads(raw)
            methods.append(command["method"])
            await asyncio.sleep(delay=0.04)
            result = {"targetInfos": [{"type": "page", "targetId": "one"}]} if len(methods) == 1 else {"sessionId": "one"}
            try:
                await websocket.send(json.dumps({"id": command["id"], "result": result}))
            except ConnectionClosed:
                return

    async with serve(handler, host="127.0.0.1", port=0) as server:
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as client:
            adapter = ChromeCdpAdapter(client=client)
            started = asyncio.get_running_loop().time()
            with pytest.raises(BrowserAdapterError, match="连接失败"):
                await adapter.connect(endpoint=f"ws://127.0.0.1:{port}/devtools/browser/one", timeout_s=0.06)
            assert asyncio.get_running_loop().time() - started < 0.1
            assert methods == ["Target.getTargets", "Target.attachToTarget"]
            await adapter.disconnect()
