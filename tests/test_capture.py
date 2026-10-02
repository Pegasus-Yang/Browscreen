"""独立像素检查、完整帧与非重叠采集调度。"""

import asyncio
from io import BytesIO

import httpx
import pytest
from PIL import Image, ImageChops

import browscreen.capture as capture_module
from browscreen.adapters.base import BrowserAdapterError, BrowserScreenshot
from browscreen.capture import CaptureService
from browscreen.imaging import compose_screenshot
from browscreen.models import MousePosition, Settings


@pytest.mark.parametrize("scale", [1, 2])
@pytest.mark.parametrize("point", [(32, 18), (32.5, 18.5), (0, 0), (127, 0), (0, 95), (127, 95)])
def test_hotspot_and_edge_clipping(scale, point, png_factory):
    raw = png_factory(width=128 * scale, height=96 * scale)
    screenshot = BrowserScreenshot(png_bytes=raw, css_viewport_width=128, css_viewport_height=96)
    result = compose_screenshot(screenshot=screenshot, mouse=MousePosition(x=point[0], y=point[1]))
    image = Image.open(BytesIO(result)).convert("RGB")
    original = Image.open(BytesIO(raw)).convert("RGB")
    expected = (round(point[0] * scale), round(point[1] * scale))
    assert image.size == (128 * scale, 96 * scale)
    assert image.getpixel(expected) == (0, 0, 0)
    box = ImageChops.difference(image, original).getbbox()
    assert box[:2] == expected


@pytest.mark.parametrize("point", [None, (-1, 20), (128, 20), (20, -1), (20, 96)])
def test_missing_or_outside_mouse_returns_clean_frame(point, png_bytes):
    screenshot = BrowserScreenshot(png_bytes=png_bytes, css_viewport_width=128, css_viewport_height=96)
    mouse = MousePosition(x=point[0], y=point[1]) if point is not None else None
    assert compose_screenshot(screenshot=screenshot, mouse=mouse) == png_bytes


def test_invalid_png_becomes_adapter_error():
    screenshot = BrowserScreenshot(png_bytes=b"not-a-png", css_viewport_width=128, css_viewport_height=96)
    with pytest.raises(BrowserAdapterError):
        compose_screenshot(screenshot=screenshot, mouse=None)


async def test_new_mouse_each_frame_and_no_expiry(tmp_path, fake_adapter, wait_for):
    (tmp_path / ".browser").write_text("first")
    mouse_file = tmp_path / ".mouse"
    mouse_file.write_text("32,18")
    async with httpx.AsyncClient() as client:
        service = CaptureService(settings=Settings(work_dir=tmp_path, interval_ms=5), adapter=fake_adapter, client=client)
        task = asyncio.create_task(service.run())
        try:
            await wait_for(lambda: service.frame_id >= 2)
            assert Image.open(BytesIO(service.current_frame.png_bytes)).getpixel((32, 18))[:3] == (0, 0, 0)
            assert mouse_file.read_text() == "32,18"
            mouse_file.write_text("64,36")
            previous = service.frame_id
            await wait_for(lambda: service.frame_id >= previous + 2)
            moved = Image.open(BytesIO(service.current_frame.png_bytes))
            assert moved.getpixel((64, 36))[:3] == (0, 0, 0)
            assert moved.getpixel((32, 18))[:3] == (42, 80, 120)
            for value in ["", "abc", "NaN,18", "-1,18"]:
                mouse_file.write_text(value)
                previous = service.frame_id
                await wait_for(lambda: service.frame_id >= previous + 2)
                assert service.current_frame.png_bytes == fake_adapter.screenshot.png_bytes
            mouse_file.unlink()
            previous = service.frame_id
            await wait_for(lambda: service.frame_id >= previous + 2)
            assert service.current_frame.png_bytes == fake_adapter.screenshot.png_bytes
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await service.disconnect()


@pytest.mark.parametrize("duration,expected", [(0.1, [0, 0.3, 0.6]), (0.5, [0, 0.5, 1])])
async def test_controlled_clock_counts_work_without_catchup(tmp_path, fake_adapter, monkeypatch, duration, expected):
    (tmp_path / ".browser").write_text("first")
    clock = [0.0]
    starts = []

    async def capture(*, timeout_s):
        starts.append(clock[0])
        clock[0] += duration
        return fake_adapter.screenshot

    async def pause(*, delay):
        clock[0] += delay
        if len(starts) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(capture_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(capture_module, "sleep", pause)
    monkeypatch.setattr(fake_adapter, "capture_viewport", capture)
    async with httpx.AsyncClient() as client:
        service = CaptureService(settings=Settings(work_dir=tmp_path), adapter=fake_adapter, client=client)
        with pytest.raises(asyncio.CancelledError):
            await service.run()
        assert starts == pytest.approx(expected)
