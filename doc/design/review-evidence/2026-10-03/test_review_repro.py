"""审核诊断：断言审核时的缺陷存在，修复后应改写为正常行为回归测试。"""

import asyncio
import json

import httpx
import pytest
from websockets.asyncio.server import serve

from browscreen.adapters.chrome_cdp import ChromeCdpAdapter
from browscreen.app import create_app
from browscreen.capture import CaptureService
from browscreen.models import Settings


@pytest.mark.asyncio
async def test_invalid_endpoint_stops_capture_after_file_is_corrected(tmp_path):
    """非法地址绕过初步校验后，HTTPX 异常会终止后台任务。"""
    endpoint = tmp_path / ".cdp"
    endpoint.write_text(data="http://☃.example", encoding="utf-8")
    requests = []

    def discovery(request):
        requests.append(request)
        return httpx.Response(status_code=503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler=discovery))
    app = create_app(
        settings=Settings(work_dir=tmp_path, interval_ms=10, connect_wait_timeout_s=0.1),
        client=client,
    )
    async with app.router.lifespan_context(app):
        task = next(task for task in asyncio.all_tasks() if task.get_name() == "browscreen-capture")
        async with asyncio.timeout(delay=1):
            while not task.done():
                await asyncio.sleep(delay=0.002)
        assert isinstance(task.exception(), httpx.InvalidURL)
        endpoint.write_text(data="http://127.0.0.1:9222", encoding="utf-8")
        await asyncio.sleep(delay=0.15)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as api:
            response = await api.get(url="/api/screenshot")
        assert requests == []
        assert response.status_code == 503
        assert response.json()["code"] == "waiting_for_browser"
        print(f"R-01: exception={type(task.exception()).__name__}, corrected_endpoint_requests={len(requests)}, elapsed_after_fix=0.15s, code={response.json()['code']}")


@pytest.mark.asyncio
async def test_persistent_screenshot_error_restarts_wait_budget(tmp_path):
    """真实本地 WebSocket 成功附着，但每次截图均返回 CDP 错误。"""
    attempts = []

    async def handler(websocket):
        attempts.append(asyncio.get_running_loop().time())
        async for raw in websocket:
            command = json.loads(s=raw)
            results = {
                "Target.getTargets": {"targetInfos": [{"type": "page", "targetId": "one"}]},
                "Target.attachToTarget": {"sessionId": "session"},
                "Runtime.evaluate": {"result": {"value": {"width": 128, "height": 96}}},
            }
            response = {"id": command["id"]}
            if "sessionId" in command:
                response["sessionId"] = command["sessionId"]
            if command["method"] == "Page.captureScreenshot":
                response["error"] = {"code": -32000, "message": "Unable to capture screenshot"}
            else:
                response["result"] = results[command["method"]]
            await websocket.send(message=json.dumps(obj=response))

    async with serve(handler, host="127.0.0.1", port=0) as server:
        port = server.sockets[0].getsockname()[1]
        (tmp_path / ".cdp").write_text(data=f"ws://127.0.0.1:{port}/devtools/browser/one", encoding="utf-8")
        async with httpx.AsyncClient(trust_env=False) as client:
            service = CaptureService(
                settings=Settings(work_dir=tmp_path, interval_ms=30, connect_wait_timeout_s=0.05),
                adapter=ChromeCdpAdapter(client=client),
                client=client,
            )
            task = asyncio.create_task(coro=service.run())
            try:
                await asyncio.sleep(delay=0.2)
                assert not task.done()
                assert service.state != "timed_out"
                assert service.current_frame is None
                assert len(attempts) > 4
                fastest = min(b - a for a, b in zip(attempts, attempts[1:]))
                assert fastest < 0.03
                print(f"R-02: wait_budget=0.05s, observed=0.2s, connects={len(attempts)}, fastest_retry={fastest:.4f}s, task_done={task.done()}, frames={service.frame_id}")
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await service.disconnect()
