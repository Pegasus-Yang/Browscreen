"""端点重读、截止时间、恢复和同目录重启验证。"""

import asyncio

import httpx
import pytest

from browscreen.adapters.base import BrowserAdapterError
from browscreen.app import create_app
from browscreen.models import Settings


async def test_bad_endpoint_then_new_address(tmp_path, fake_adapter, wait_for, monkeypatch):
    endpoint = tmp_path / ".browser"
    endpoint.write_text("bad")
    original_connect = fake_adapter.connect

    async def connect(*, endpoint, timeout_s):
        await original_connect(endpoint=endpoint, timeout_s=timeout_s)
        if endpoint == "bad":
            raise BrowserAdapterError("地址不可用")

    monkeypatch.setattr(fake_adapter, "connect", connect)
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5, connect_wait_timeout_s=0.2), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        await wait_for(lambda: len(fake_adapter.connections) >= 2)
        endpoint.write_text("new")
        await wait_for(lambda: app.state.capture.current_frame is not None)
        assert fake_adapter.connections[-1] == "new"
        assert fake_adapter.disconnects >= 2
        assert all(0 < budget <= 0.2 for budget in fake_adapter.budgets)


@pytest.mark.parametrize("change_address", [False, True])
async def test_recovery_preserves_registration_and_mouse(tmp_path, fake_adapter, wait_for, change_address):
    endpoint = tmp_path / ".browser"
    mouse = tmp_path / ".mouse"
    endpoint.write_text("first")
    mouse.write_text("32,18")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler=lambda request: httpx.Response(status_code=204)))
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5, connect_wait_timeout_s=0.2), adapter=fake_adapter, client=client)
    async with app.router.lifespan_context(app):
        service = app.state.capture
        service.webhooks.add("http://receiver/frames")
        await wait_for(lambda: service.current_frame is not None)
        previous_id = service.frame_id
        fake_adapter.fail_connect = True
        fake_adapter.fail_capture = True
        await wait_for(lambda: service.state == "waiting" and service.current_frame is None)
        assert mouse.read_text() == "32,18"
        if change_address:
            endpoint.write_text("second")
        fake_adapter.fail_connect = False
        await wait_for(lambda: service.current_frame is not None and service.frame_id > previous_id)
        assert fake_adapter.connections[-1] == ("second" if change_address else "first")
        assert service.webhooks == {"http://receiver/frames"}
        assert mouse.read_text() == "32,18"
    assert mouse.stat().st_size == 0
    assert client.is_closed


async def test_failures_do_not_reset_deadline_or_poll_after_timeout(tmp_path, fake_adapter, wait_for):
    (tmp_path / ".browser").write_text("bad")
    fake_adapter.fail_connect = True
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5, connect_wait_timeout_s=0.04), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        started = asyncio.get_running_loop().time()
        await wait_for(lambda: app.state.capture.state == "timed_out")
        assert asyncio.get_running_loop().time() - started < 0.15
        attempts = len(fake_adapter.connections)
        assert attempts >= 2
        fake_adapter.fail_connect = False
        (tmp_path / ".browser").write_text("fixed")
        await asyncio.sleep(delay=0.02)
        assert len(fake_adapter.connections) == attempts
    restarted = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5), adapter=fake_adapter)
    async with restarted.router.lifespan_context(restarted):
        await wait_for(lambda: restarted.state.capture.current_frame is not None)
        assert fake_adapter.connections[-1] == "fixed"
        assert not restarted.state.capture.webhooks


async def test_same_directory_restart_has_no_previous_cursor(tmp_path, fake_adapter, wait_for):
    (tmp_path / ".browser").write_text("first")
    mouse = tmp_path / ".mouse"
    mouse.write_text("32,18")
    settings = Settings(work_dir=tmp_path, interval_ms=5)
    app = create_app(settings=settings, adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.current_frame is not None)
        assert app.state.capture.current_frame.png_bytes != fake_adapter.screenshot.png_bytes
    assert mouse.exists() and mouse.stat().st_size == 0
    restarted = create_app(settings=settings, adapter=fake_adapter)
    async with restarted.router.lifespan_context(restarted):
        await wait_for(lambda: restarted.state.capture.current_frame is not None)
        assert restarted.state.capture.current_frame.png_bytes == fake_adapter.screenshot.png_bytes
        mouse.write_text("64,36")
        previous = restarted.state.capture.frame_id
        await wait_for(lambda: restarted.state.capture.frame_id > previous + 1)
        assert restarted.state.capture.current_frame.png_bytes != fake_adapter.screenshot.png_bytes
