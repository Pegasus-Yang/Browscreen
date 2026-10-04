"""高频失败日志去重与状态转换验证。"""

import logging

import pytest

from browscreen.adapters.base import BrowserAdapterError
from browscreen.app import create_app
from browscreen.models import Settings


@pytest.mark.parametrize("verbose", [False, True])
async def test_repeated_connection_failures_only_have_debug_details(tmp_path, fake_adapter, wait_for, caplog, verbose):
    caplog.set_level(level=logging.DEBUG if verbose else logging.INFO, logger="browscreen.capture")
    (tmp_path / ".browser").write_text(data="unavailable", encoding="utf-8")
    fake_adapter.fail_connect = True
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5, connect_wait_timeout_s=0.05), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.state == "timed_out")
        assert len(fake_adapter.connections) >= 2
    records = [record for record in caplog.records if record.name == "browscreen.capture"]
    assert sum("开始等待浏览器画面" in record.message for record in records) == 1
    assert sum("等待浏览器连接超时" in record.message for record in records) == 1
    details = [record for record in records if "浏览器暂不可用" in record.message]
    assert len(details) == (len(fake_adapter.connections) if verbose else 0)
    assert all(record.levelno == logging.DEBUG for record in details)


async def test_capture_recovery_logs_one_warning_per_failure_cycle(tmp_path, fake_adapter, wait_for, monkeypatch, caplog):
    caplog.set_level(level=logging.INFO, logger="browscreen.capture")
    (tmp_path / ".browser").write_text(data="available", encoding="utf-8")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5, connect_wait_timeout_s=0.1), adapter=fake_adapter)
    original_capture = fake_adapter.capture_viewport
    attempts = []

    async def fail(*, timeout_s):
        attempts.append(timeout_s)
        raise BrowserAdapterError("持续截图失败")

    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.current_frame is not None)
        monkeypatch.setattr(fake_adapter, "capture_viewport", fail)
        await wait_for(lambda: len(attempts) >= 3)
        assert app.state.capture.current_frame is None
        monkeypatch.setattr(fake_adapter, "capture_viewport", original_capture)
        await wait_for(lambda: app.state.capture.current_frame is not None)
        fake_adapter.fail_capture = True
        previous_id = app.state.capture.frame_id
        await wait_for(lambda: app.state.capture.frame_id > previous_id)
    records = [record for record in caplog.records if record.name == "browscreen.capture"]
    assert sum("浏览器画面已就绪" in record.message for record in records) == 1
    assert sum("浏览器采集失效，开始恢复" in record.message for record in records) == 2
    assert sum("浏览器画面已恢复" in record.message for record in records) == 2
    assert not any(record.message == "浏览器已连接" for record in records)
