"""可选依赖隔离、实际视频时间轴及取消后的收尾回归。"""

import asyncio
import logging
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

import browscreen.main as cli_module
import browscreen.recording as recording_module
from browscreen.app import create_app
from browscreen.main import parse_settings
from browscreen.models import CurrentFrame, Settings
from browscreen.recording import RecordingStartupError, VideoRecorder, load_video_backend


@pytest.fixture
def video_backend():
    return pytest.importorskip(modname="av", reason="真实视频测试需要安装 video extra")


@pytest.fixture
def stub_backend(monkeypatch):
    backend = SimpleNamespace(Codec=lambda **kwargs: object())
    monkeypatch.setattr(recording_module, "import_module", lambda **kwargs: backend)
    return backend


def make_frame(*, png: bytes, number: int = 1) -> CurrentFrame:
    return CurrentFrame(frame_id=number, capture_started_at="2026-10-05T00:00:00Z", png_bytes=png)


def test_record_defaults_and_output_resolution(tmp_path, monkeypatch):
    settings = parse_settings(argv=["--work-dir", str(tmp_path)])
    assert not settings.record and settings.record_output is None
    monkeypatch.chdir(tmp_path)
    settings = parse_settings(argv=["--work-dir", str(tmp_path), "--record", "--record-output", "session.MP4"])
    assert settings.record and settings.record_output == tmp_path / "session.MP4"
    assert not settings.record_output.exists()
    settings = Settings(work_dir=tmp_path, record=True, record_output="~/browscreen-recording-test.mp4")
    assert settings.record_output == Path.home() / "browscreen-recording-test.mp4"


@pytest.mark.parametrize("output", ["session.mp4", "session.webm", "missing/session.mp4", ""])
def test_invalid_record_settings(tmp_path, output):
    with pytest.raises(ValidationError):
        Settings(work_dir=tmp_path, record=output != "session.mp4", record_output=tmp_path / output)


def test_output_without_switch_is_cli_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        parse_settings(argv=["--work-dir", str(tmp_path), "--record-output", str(tmp_path / "session.mp4")])
    assert error.value.code == 2
    assert "必须同时提供 --record" in capsys.readouterr().err


def test_missing_dependency_exits_with_friendly_install_hint(tmp_path, monkeypatch, caplog):
    def missing(*, name):
        raise ModuleNotFoundError("No module named 'av'", name="av")

    monkeypatch.setattr(recording_module, "import_module", missing)
    monkeypatch.setattr(sys, "argv",
                        ["browscreen", "--work-dir", str(tmp_path), "--record"])
    monkeypatch.setattr(cli_module.uvicorn, "run", lambda **kwargs: pytest.fail("缺少依赖时不得启动服务"))
    with pytest.raises(SystemExit) as error:
        cli_module.main()
    assert error.value.code == 1
    assert "uv sync --extra video" in caplog.text
    assert "browscreen[video]" in caplog.text
    assert "移除 --record" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_unavailable_encoder_has_specific_message(monkeypatch):
    def codec(**kwargs):
        raise ValueError("encoder missing")

    monkeypatch.setattr(recording_module, "import_module", lambda **kwargs: SimpleNamespace(Codec=codec))
    with pytest.raises(RecordingStartupError, match="libx264 编码器不可用"):
        load_video_backend()


async def test_disabled_recording_does_not_load_backend(tmp_path, fake_adapter, wait_for, monkeypatch):
    monkeypatch.setattr(recording_module, "import_module", lambda **kwargs: pytest.fail("未开启录制不应加载 av"))
    (tmp_path / ".browser").write_text("ready")
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=5), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.frame_id >= 2)
        assert app.state.capture.recorder is None
    assert not list(tmp_path.glob("*.mp4"))


async def test_existing_file_is_preserved(tmp_path, stub_backend):
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"keep this video")
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=output))
    with pytest.raises(RecordingStartupError, match="文件不存在"):
        await recorder.prepare()
    assert output.read_bytes() == b"keep this video"


async def test_file_creation_permission_error_is_friendly(tmp_path, stub_backend, monkeypatch):
    output = tmp_path / "session.mp4"
    original = Path.open

    def fail(path, *args, **kwargs):
        if path == output:
            raise PermissionError("permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=output))
    with pytest.raises(RecordingStartupError, match="请确认目录可写"):
        await recorder.prepare()
    assert not output.exists()


async def test_zero_frames_removes_reserved_file_and_reports_once(tmp_path, stub_backend, caplog):
    caplog.set_level(level=logging.INFO)
    output = tmp_path / "empty.mp4"
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=output))
    await recorder.prepare()
    assert output.exists()
    await recorder.finish(stopped_at=100)
    await recorder.finish(stopped_at=200)
    recorder.report()
    recorder.report()
    assert recorder.closed and recorder._file.closed
    assert not output.exists()
    assert caplog.text.count("未生成视频：未获取到有效截图") == 1


@pytest.mark.parametrize("times,end", [([100], 100.8), ([100, 100.3, 101.2], 101.75), ([100, 100.0001], 100.01)])
async def test_real_video_preserves_timestamps_and_last_frame(tmp_path, video_backend, png_factory, caplog, times, end):
    caplog.set_level(level=logging.INFO)
    output = tmp_path / "session.mp4"
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=output))
    await recorder.prepare()
    colors = [(220, 30, 30), (30, 220, 30), (30, 30, 220)]
    for number, time in enumerate(times):
        await recorder.append(frame=make_frame(png=png_factory(color=colors[number]), number=number + 1), captured_at=time)
    await recorder.finish(stopped_at=end)
    await recorder.finish(stopped_at=end + 100)
    recorder.report()
    assert recorder.error is None and recorder.frame_count == len(times)
    with video_backend.open(file=str(output)) as video:
        stream = video.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert stream.codec_context.format.name == "yuv420p"
        frames = list(video.decode(video=0))
        assert len(frames) == len(times)
        expected = [max(round((time - times[0]) * 1000), number) / 1000 for number, time in enumerate(times)]
        assert [float(frame.pts * frame.time_base) for frame in frames] == pytest.approx(expected, abs=0.001)
        assert float(stream.duration * stream.time_base) == pytest.approx(end - times[0], abs=0.001)
        assert all(frame.duration > 0 for frame in frames)
        for frame, color in zip(frames, colors):
            pixel = frame.to_image().getpixel((50, 50))
            assert max(abs(a - b) for a, b in zip(pixel, color)) < 8
    assert f"录制完成，视频文件：{output}" in caplog.text


async def test_real_video_adapts_odd_and_changed_sizes(tmp_path, video_backend, png_factory):
    output = tmp_path / "size.mp4"
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=output))
    await recorder.prepare()
    for number, size in enumerate([(127, 95), (200, 50), (50, 200)]):
        await recorder.append(frame=make_frame(png=png_factory(width=size[0], height=size[1], color=(230, 230, 230)), number=number + 1),
                              captured_at=100 + number)
    await recorder.finish(stopped_at=103)
    assert recorder.error is None
    with video_backend.open(file=str(output)) as video:
        frames = [frame.to_image() for frame in video.decode(video=0)]
    assert all(frame.size == (128, 96) for frame in frames)
    assert min(frames[0].getpixel((50, 50))) > 220
    assert max(frames[1].getpixel((64, 10))) < 8
    assert min(frames[1].getpixel((64, 48))) > 220
    assert max(frames[2].getpixel((10, 48))) < 8
    assert min(frames[2].getpixel((64, 48))) > 220


async def test_temp_outputs_are_unique_and_remain_after_exit(tmp_path, video_backend, png_bytes, monkeypatch):
    monkeypatch.setattr(recording_module.tempfile, "tempdir", str(tmp_path))
    recorders = [VideoRecorder(settings=Settings(work_dir=tmp_path, record=True)) for _ in range(2)]
    for recorder in recorders:
        await recorder.prepare()
        await recorder.append(frame=make_frame(png=png_bytes), captured_at=100)
        await recorder.finish(stopped_at=101)
        assert recorder.path.parent == tmp_path and recorder.path.is_absolute() and recorder.path.exists()
    assert recorders[0].path != recorders[1].path


@pytest.mark.parametrize("fail_worker", [False, True])
async def test_cancelled_append_finishes_worker_before_closing(tmp_path, stub_backend, png_bytes, wait_for, monkeypatch, fail_worker):
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=tmp_path / "cancel.mp4"))
    await recorder.prepare()
    started, release, finished = Event(), Event(), Event()
    original = recorder._close

    def write(**kwargs):
        started.set()
        assert release.wait(timeout=2)
        finished.set()
        if fail_worker:
            raise OSError("late disk failure")

    def close(**kwargs):
        assert finished.is_set()
        original(**kwargs)

    monkeypatch.setattr(recorder, "_append", write)
    monkeypatch.setattr(recorder, "_close", close)
    append = asyncio.create_task(coro=recorder.append(frame=make_frame(png=png_bytes), captured_at=100))
    finishing = None
    try:
        await wait_for(started.is_set)
        append.cancel()
        with pytest.raises(asyncio.CancelledError):
            await append
        finishing = asyncio.create_task(coro=recorder.finish(stopped_at=101))
        await asyncio.sleep(delay=0.02)
        assert not finishing.done() and not recorder._file.closed
        release.set()
        await finishing
        assert recorder.closed and recorder._file.closed
        assert bool(recorder.error) is fail_worker
    finally:
        release.set()
        if finishing is not None:
            await finishing


async def test_write_failure_does_not_stop_http_capture_or_webhooks(tmp_path, fake_adapter, video_backend, wait_for, monkeypatch, caplog):
    caplog.set_level(level=logging.INFO)
    (tmp_path / ".browser").write_text("ready")
    (tmp_path / ".mouse").write_text("32,18")
    (tmp_path / ".cdp").write_text("keep")
    output = tmp_path / "partial.mp4"
    original = VideoRecorder._append
    delivered = []

    def fail_second(recorder, *, frame, captured_at):
        if frame.frame_id == 2:
            raise OSError("simulated full disk")
        original(recorder, frame=frame, captured_at=captured_at)

    def webhook(request):
        delivered.append(request.headers["x-frame-id"])
        return httpx.Response(status_code=200)

    monkeypatch.setattr(VideoRecorder, "_append", fail_second)
    client = httpx.AsyncClient(transport=httpx.MockTransport(webhook))
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=20, record=True, record_output=output),
                     adapter=fake_adapter, client=client)
    async with app.router.lifespan_context(app):
        app.state.capture.webhooks.add("http://receiver/frame")
        await wait_for(lambda: app.state.capture.frame_id >= 4)
        assert app.state.capture.recorder.closed
        assert len(delivered) >= 3
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as preview:
            assert (await preview.get("/api/screenshot")).status_code == 200
    assert "已保存部分视频" in caplog.text and "simulated full disk" in caplog.text
    assert (tmp_path / ".mouse").read_text() == "" and (tmp_path / ".cdp").read_text() == "keep"
    with video_backend.open(file=str(output)) as video:
        image = next(video.decode(video=0)).to_image()
        assert min(image.getpixel((32, 18))) < 15


async def test_timeout_finalizes_video_before_http_exit(tmp_path, fake_adapter, video_backend, wait_for):
    (tmp_path / ".browser").write_text("ready")
    output = tmp_path / "timeout.mp4"
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10, connect_wait_timeout_s=0.05,
                                      record=True, record_output=output), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        await wait_for(lambda: app.state.capture.frame_id >= 2)
        fake_adapter.fail_connect = True
        fake_adapter.fail_capture = True
        await wait_for(lambda: app.state.capture.recorder.closed)
        assert app.state.capture.state == "timed_out"
        recorder = app.state.capture.recorder
        with video_backend.open(file=str(output)) as video:
            duration = float(video.streams.video[0].duration * video.streams.video[0].time_base)
            assert list(video.decode(video=0))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as preview:
            assert (await preview.get("/")).status_code == 200
            assert (await preview.get("/api/screenshot")).json()["code"] == "browser_wait_timeout"
        await asyncio.sleep(delay=0.03)
    with video_backend.open(file=str(output)) as video:
        assert float(video.streams.video[0].duration * video.streams.video[0].time_base) == duration
    assert recorder.stop_reason


@pytest.mark.parametrize("phase", ["flush", "close", "file"])
async def test_finalization_failure_is_reported_and_file_is_closed(tmp_path, video_backend, png_bytes, caplog, phase):
    caplog.set_level(level=logging.INFO)
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=tmp_path / "failure.mp4"))
    await recorder.prepare()
    await recorder.append(frame=make_frame(png=png_bytes), captured_at=100)
    if phase == "flush":
        stream = recorder._stream

        def encode(*, frame):
            if frame is None:
                raise OSError("simulated flush failure")
            return stream.encode(frame=frame)

        recorder._stream = SimpleNamespace(encode=encode)
    elif phase == "close":
        container = recorder._container

        def close():
            container.close()
            raise OSError("simulated close failure")

        recorder._container = SimpleNamespace(mux=container.mux, close=close)
    else:
        file = recorder._file

        class FailingFile:
            @property
            def closed(self):
                return file.closed

            def close(self):
                file.close()
                raise OSError("simulated file failure")

        recorder._file = FailingFile()
    await recorder.finish(stopped_at=101)
    recorder.report()
    assert recorder.closed and recorder._file.closed and recorder.error
    assert f"simulated {phase} failure" in caplog.text
    assert "录制完成" not in caplog.text


async def test_cancelled_finish_keeps_one_close_operation(tmp_path, stub_backend, wait_for, monkeypatch):
    recorder = VideoRecorder(settings=Settings(work_dir=tmp_path, record=True, record_output=tmp_path / "cancel-close.mp4"))
    await recorder.prepare()
    started, release = Event(), Event()
    calls = []
    original = recorder._close

    def close(**kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(timeout=2)
        original(**kwargs)

    monkeypatch.setattr(recorder, "_close", close)
    first = asyncio.create_task(coro=recorder.finish(stopped_at=101))
    try:
        await wait_for(started.is_set)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        await recorder.finish(stopped_at=200)
        assert recorder.closed and len(calls) == 1
        assert calls[0]["stopped_at"] == 101
    finally:
        release.set()
        await recorder.finish(stopped_at=200)
