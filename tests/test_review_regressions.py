"""审核问题的端点、连续截图失败与异常状态回归。"""

import asyncio
import base64
import json
import logging
from threading import Event

import httpx
import pytest
from websockets.asyncio.server import serve

import browscreen.capture as capture_module
from browscreen.app import create_app
from browscreen.models import Settings


@pytest.fixture
async def cdp_server(png_bytes):
    """提供可切换截图故障的本地 CDP 服务。"""
    state = {"failure": None, "attempts": []}

    async def handler(websocket):
        state["attempts"].append(asyncio.get_running_loop().time())
        async for raw in websocket:
            command = json.loads(s=raw)
            results = {
                "Target.getTargets": {"targetInfos": [{"type": "page", "targetId": "one"}]},
                "Target.attachToTarget": {"sessionId": "session"},
                "Runtime.evaluate": {"result": {"value": {"width": 128, "height": 96}}},
                "Page.captureScreenshot": {"data": base64.b64encode(s=b"bad-png" if state["failure"] == "png" else png_bytes).decode()},
            }
            response = {"id": command["id"], "result": results[command["method"]]}
            if "sessionId" in command:
                response["sessionId"] = command["sessionId"]
            if command["method"] == "Page.captureScreenshot" and state["failure"] == "screenshot":
                response.pop("result")
                response["error"] = {"code": -32000, "message": "Unable to capture screenshot"}
            await websocket.send(message=json.dumps(obj=response))

    async with serve(handler, host="127.0.0.1", port=0) as server:
        port = server.sockets[0].getsockname()[1]
        state["url"] = f"ws://127.0.0.1:{port}/devtools/browser/one"
        yield state


@pytest.mark.parametrize("endpoint", ["http://☃.example", "http://\x00host/", "http://[v1.example]/"])
async def test_invalid_httpx_endpoint_recovers_after_file_correction(tmp_path, cdp_server, endpoint, caplog, wait_for):
    caplog.set_level(level=logging.INFO, logger="browscreen.capture")
    path = tmp_path / ".cdp"
    path.write_text(data=endpoint, encoding="utf-8")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10, connect_wait_timeout_s=0.5))
    async with app.router.lifespan_context(app):
        await wait_for(lambda: "Chrome 连接失败" in caplog.text)
        assert app.state.capture.state == "waiting"
        path.write_text(data=cdp_server["url"], encoding="utf-8")
        await wait_for(lambda: app.state.capture.current_frame is not None)


@pytest.mark.parametrize("endpoint", ["http://☃.example", "http://\x00host/"])
async def test_invalid_httpx_endpoint_exhausts_wait_budget(tmp_path, endpoint, wait_for):
    (tmp_path / ".cdp").write_text(data=endpoint, encoding="utf-8")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10, connect_wait_timeout_s=0.04))
    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.state == "timed_out")
        assert app.state.capture.unavailable_error().code == "browser_wait_timeout"


@pytest.mark.parametrize("failure", ["screenshot", "png"])
async def test_persistent_capture_failure_is_bounded_and_throttled(tmp_path, cdp_server, failure, wait_for):
    cdp_server["failure"] = failure
    (tmp_path / ".cdp").write_text(data=cdp_server["url"], encoding="utf-8")
    mouse = tmp_path / ".mouse"
    mouse.write_text(data="32,18", encoding="utf-8")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=20, connect_wait_timeout_s=0.12))
    async with app.router.lifespan_context(app):
        started = asyncio.get_running_loop().time()
        await wait_for(lambda: app.state.capture.state == "timed_out")
        assert asyncio.get_running_loop().time() - started < 0.3
        attempts = cdp_server["attempts"]
        assert 2 <= len(attempts) <= 7
        assert all(b - a >= 0.018 for a, b in zip(attempts, attempts[1:]))
        assert app.state.capture.current_frame is None
        count = len(attempts)
        await asyncio.sleep(delay=0.03)
        assert len(attempts) == count
        assert mouse.read_text(encoding="utf-8") == "32,18"


async def test_recovery_succeeds_before_deadline_and_next_failure_gets_new_budget(tmp_path, cdp_server, wait_for):
    cdp_server["failure"] = "screenshot"
    (tmp_path / ".cdp").write_text(data=cdp_server["url"], encoding="utf-8")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10, connect_wait_timeout_s=0.12))
    async with app.router.lifespan_context(app):
        await wait_for(lambda: len(cdp_server["attempts"]) >= 2)
        cdp_server["failure"] = None
        await wait_for(lambda: app.state.capture.current_frame is not None)
        # 超过最初的截止时间后再故障，应当有一轮新的恢复机会。
        await asyncio.sleep(delay=0.15)
        previous = app.state.capture.frame_id
        cdp_server["failure"] = "screenshot"
        await wait_for(lambda: app.state.capture.current_frame is None)
        cdp_server["failure"] = None
        await wait_for(lambda: app.state.capture.current_frame is not None)
        assert app.state.capture.frame_id > previous


async def test_first_capture_uses_remaining_recovery_budget(tmp_path, fake_adapter, wait_for):
    (tmp_path / ".browser").write_text(data="first", encoding="utf-8")
    fake_adapter.delay = 1
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10, connect_wait_timeout_s=0.04), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        started = asyncio.get_running_loop().time()
        await wait_for(lambda: app.state.capture.state == "timed_out")
        assert asyncio.get_running_loop().time() - started < 0.2
        assert app.state.capture.current_frame is None


async def test_first_png_composition_cannot_outlive_recovery_budget(tmp_path, fake_adapter, wait_for, monkeypatch):
    (tmp_path / ".browser").write_text(data="first", encoding="utf-8")
    started, release = Event(), Event()
    original = capture_module.compose_screenshot

    def compose(*, screenshot, mouse):
        started.set()
        release.wait(timeout=1)
        return original(screenshot=screenshot, mouse=mouse)

    monkeypatch.setattr(capture_module, "compose_screenshot", compose)
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10, connect_wait_timeout_s=0.06), adapter=fake_adapter)
    try:
        async with app.router.lifespan_context(app):
            await wait_for(started.is_set)
            await wait_for(lambda: app.state.capture.state == "timed_out")
            release.set()
            await asyncio.sleep(delay=0.03)
            assert app.state.capture.current_frame is None
            assert app.state.capture.frame_id == 0
    finally:
        release.set()


async def test_unexpected_task_error_clears_old_frame_and_reports_immediately(tmp_path, fake_adapter, wait_for, monkeypatch, caplog):
    (tmp_path / ".browser").write_text(data="first", encoding="utf-8")
    mouse = tmp_path / ".mouse"
    mouse.write_text(data="32,18", encoding="utf-8")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.current_frame is not None)

        async def fail(*, timeout_s):
            raise RuntimeError("模拟未预期的采集错误")

        monkeypatch.setattr(fake_adapter, "capture_viewport", fail)
        await wait_for(lambda: app.state.capture.state == "failed")
        assert "采集任务异常停止" in caplog.text
        assert "模拟未预期的采集错误" in caplog.text
        assert app.state.capture.current_frame is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as client:
            response = await client.get(url="/api/screenshot")
            assert response.status_code == 503
            assert response.json()["code"] == "capture_failed"
        assert mouse.read_text(encoding="utf-8") == "32,18"
    assert mouse.read_text(encoding="utf-8") == ""
