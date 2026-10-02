"""HTTP 状态、共享缓存与生命周期清理验证。"""

import asyncio
from io import BytesIO
from threading import Event

import httpx
from PIL import Image

import browscreen.capture as capture_module
from browscreen.app import create_app
from browscreen.models import Settings


async def test_waiting_connected_and_frame_contract(tmp_path, fake_adapter, wait_for):
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=500), adapter=fake_adapter)
    fake_adapter.delay = 0.08
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as client:
            assert (await client.get("/api/screenshot")).json()["code"] == "waiting_for_browser"
            (tmp_path / ".browser").write_text("first")
            await wait_for(lambda: app.state.capture.state == "connected")
            assert (await client.get("/api/screenshot")).json()["code"] == "screenshot_not_ready"
            await wait_for(lambda: app.state.capture.current_frame is not None)
            count = fake_adapter.captures
            frame = app.state.capture.current_frame
            responses = await asyncio.gather(*(client.get("/api/screenshot") for _ in range(20)))
            assert fake_adapter.captures == count
            for response in responses:
                assert response.status_code == 200
                assert response.headers["content-type"] == "image/png"
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["x-frame-id"] == str(frame.frame_id)
                assert response.headers["x-capture-started-at"] == frame.capture_started_at
                assert frame.capture_started_at.endswith("Z")
                assert response.content == frame.png_bytes
                assert Image.open(BytesIO(response.content)).size == (128, 96)
            assert (await client.get("/")).status_code == 200


async def test_timeout_preserves_http_and_mouse_until_exit(tmp_path, fake_adapter, wait_for):
    mouse = tmp_path / ".mouse"
    mouse.write_text("32,18")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5, connect_wait_timeout_s=0.025), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.state == "timed_out")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as client:
            response = await client.get("/api/screenshot")
            assert response.status_code == 503
            assert response.json()["code"] == "browser_wait_timeout"
            assert (await client.get("/")).status_code == 200
            (tmp_path / ".browser").write_text("too-late")
            await asyncio.sleep(delay=0.02)
            assert not fake_adapter.connections
            assert mouse.read_text() == "32,18"
    assert mouse.exists() and mouse.stat().st_size == 0


async def test_cleanup_failure_still_clears_mouse(tmp_path, fake_adapter, monkeypatch, caplog):
    mouse = tmp_path / ".mouse"
    endpoint = tmp_path / ".cdp"
    mouse.write_text("32,18")
    endpoint.write_text("keep")

    async def fail_disconnect():
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(fake_adapter, "disconnect", fail_disconnect)
    app = create_app(settings=Settings(work_dir=tmp_path), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        pass
    assert mouse.stat().st_size == 0
    assert endpoint.read_text() == "keep"
    assert "连接清理失败" in caplog.text


async def test_png_worker_keeps_http_responsive(tmp_path, fake_adapter, wait_for, monkeypatch):
    (tmp_path / ".browser").write_text("first")
    started, release = Event(), Event()
    original = capture_module.compose_screenshot

    def compose(*, screenshot, mouse):
        started.set()
        release.wait(timeout=1)
        return original(screenshot=screenshot, mouse=mouse)

    monkeypatch.setattr(capture_module, "compose_screenshot", compose)
    app = create_app(settings=Settings(work_dir=tmp_path), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        try:
            await wait_for(started.is_set)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as client:
                async with asyncio.timeout(delay=0.1):
                    assert (await client.get("/")).status_code == 200
                    assert (await client.get("/api/screenshot")).json()["code"] == "screenshot_not_ready"
            release.set()
            await wait_for(lambda: app.state.capture.current_frame is not None)
        finally:
            release.set()
