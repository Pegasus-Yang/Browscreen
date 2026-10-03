"""一次性真实 Chrome 预览恢复核验，仅启动并回收本次自建实例。"""

import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

import httpx
from fastapi.responses import HTMLResponse
from PIL import Image
import uvicorn
from websockets.asyncio.client import connect

from browscreen.adapters.base import BrowserScreenshot
from browscreen.app import create_app
from browscreen.models import Settings


class Adapter:
    endpoint_file_name = ".browser"

    async def connect(self, *, endpoint, timeout_s):
        pass

    async def disconnect(self):
        pass

    async def capture_viewport(self, *, timeout_s):
        return screenshot


data = io.BytesIO()
Image.new(mode="RGB", size=(128, 96), color=(42, 80, 120)).save(fp=data, format="PNG")
screenshot = BrowserScreenshot(png_bytes=data.getvalue(), css_viewport_width=128, css_viewport_height=96)


async def main():
    with tempfile.TemporaryDirectory(prefix="browscreen-preview-") as directory:
        root = Path(directory)
        (root / ".browser").write_text(data="test", encoding="utf-8")
        app = create_app(settings=Settings(work_dir=root, interval_ms=30), adapter=Adapter())
        cache_test = os.environ.get("PREVIEW_ALLOW_BFCACHE") == "1"
        if cache_test:
            @app.middleware("http")
            async def allow_test_cache(request, call_next):
                response = await call_next(request)
                if "cache-control" in response.headers:
                    del response.headers["cache-control"]
                return response

        @app.get("/away")
        async def away():
            return HTMLResponse(content="<!doctype html><title>Away</title><p>Away</p>")

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(config=uvicorn.Config(app=app, log_level="error"))
        server_task = asyncio.create_task(coro=server.serve(sockets=[sock]))
        process = None
        try:
            async with asyncio.timeout(delay=5):
                while not server.started:
                    await asyncio.sleep(delay=0.01)
            with (root / "chrome.log").open(mode="wb") as log:
                process = subprocess.Popen(args=[
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "--headless", "--no-first-run", "--no-default-browser-check",
                    "--remote-debugging-port=0", f"--user-data-dir={root / 'profile'}", "about:blank",
                ], stdout=log, stderr=log)
                active_port = root / "profile/DevToolsActivePort"
                async with asyncio.timeout(delay=10):
                    while not active_port.exists():
                        if process.poll() is not None:
                            raise RuntimeError((root / "chrome.log").read_text())
                        await asyncio.sleep(delay=0.05)
                debug_port = int(active_port.read_text().splitlines()[0])
                async with httpx.AsyncClient(trust_env=False) as client:
                    targets = (await client.get(url=f"http://127.0.0.1:{debug_port}/json/list")).json()
                page = next(item for item in targets if item["type"] == "page")
                events = []
                async with connect(uri=page["webSocketDebuggerUrl"], proxy=None) as ws:
                    command_id = 0

                    async def command(method, params=None):
                        nonlocal command_id
                        command_id += 1
                        await ws.send(message=json.dumps(obj={"id": command_id, "method": method, "params": params or {}}))
                        async with asyncio.timeout(delay=10):
                            while True:
                                message = json.loads(s=await ws.recv())
                                if message.get("id") == command_id:
                                    assert "error" not in message, message
                                    return message.get("result", {})
                                events.append(message)

                    async def evaluate(expression):
                        result = await command("Runtime.evaluate", {"expression": expression, "returnByValue": True})
                        return result.get("result", {}).get("value")

                    async def until(expression):
                        async with asyncio.timeout(delay=5):
                            while not (value := await evaluate(expression)):
                                await asyncio.sleep(delay=0.03)
                        return value

                    await command("Page.enable")
                    await command("Page.addScriptToEvaluateOnNewDocument", {"source": "window.addEventListener('pageshow', e => { window.restorePersisted = e.persisted; });"})
                    await command("Page.navigate", {"url": f"http://127.0.0.1:{port}/"})
                    first = await until("document.getElementById('metadata')?.textContent")
                    await command("Page.navigate", {"url": f"http://localhost:{port}/away"})
                    await until("document.title === 'Away'")
                    history = await command("Page.getNavigationHistory")
                    await command("Page.navigateToHistoryEntry", {"entryId": history["entries"][history["currentIndex"] - 1]["id"]})
                    second = await until("document.getElementById('metadata')?.textContent")
                    persisted = await evaluate("window.restorePersisted")
                    await asyncio.sleep(delay=0.15)
                    third = await evaluate("document.getElementById('metadata').textContent")
                    result = {"test_cache_headers_removed": cache_test, "persisted": persisted, "first": first, "restored": second, "later": third, "notRestored": [e for e in events if e.get("method") == "Page.backForwardCacheNotUsed"]}
                    print(json.dumps(obj=result, ensure_ascii=False, indent=2))
                    if cache_test:
                        assert persisted is True, "未命中真实 bfcache"
                    assert second != third, "恢复后帧未继续更新"
        finally:
            if process is not None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
            server.should_exit = True
            await server_task
            sock.close()


asyncio.run(main())
