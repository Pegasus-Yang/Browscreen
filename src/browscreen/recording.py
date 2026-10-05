"""按实际采样时间将合成 PNG 串行录制为 MP4。"""

import asyncio
import logging
import os
import tempfile
from fractions import Fraction
from importlib import import_module
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import BinaryIO

from PIL import Image, ImageOps

from browscreen.models import CurrentFrame, Settings

logger = logging.getLogger(__name__)
TIME_BASE = Fraction(1, 1000)


class RecordingStartupError(RuntimeError):
    """录制依赖或输出文件无法准备。"""


def load_video_backend() -> ModuleType:
    """仅在开启录制时加载 PyAV 并检查 H.264 编码器。

    :returns: 可用的 av 模块。
    :raises RecordingStartupError: 缺少可选依赖或编码器不可用。
    """
    try:
        backend = import_module(name="av")
    except (ImportError, OSError) as error:
        raise RecordingStartupError(
            "无法开启视频录制：缺少或无法加载 PyAV 可选依赖。"
            "源码环境请运行 uv sync --extra video；安装包环境请运行 "
            "uv pip install 'browscreen[video]'。安装时使用 "
            "-i http://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com。"
            "不需要录制时，请移除 --record 和 --record-output。"
        ) from error
    try:
        backend.Codec(name="libx264", mode="w")
    except Exception as error:
        raise RecordingStartupError(f"无法开启视频录制：PyAV 中的 libx264 编码器不可用，请检查 video 可选依赖：{error}") from error
    return backend


class VideoRecorder:
    """拥有一个输出文件，保留一张待编码帧并顺序写入。

    :param settings: 开启录制的实例配置。
    """

    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings
        self.path: Path | None = None
        self.error: str | None = None
        self.stop_reason: str | None = None
        self.frame_count = 0
        self.closed = False
        self._backend: ModuleType | None = None
        self._file: BinaryIO | None = None
        self._file_identity: tuple[int, int] | None = None
        self._container = None
        self._stream = None
        self._source_size: tuple[int, int] | None = None
        self._canvas_size: tuple[int, int] | None = None
        self._epoch: float | None = None
        self._pending = None
        self._durations: dict[int, int] = {}
        self._operation: asyncio.Task | None = None
        self._finish_task: asyncio.Task | None = None
        self._finalization_failed = False
        self._has_output = False
        self._reported = False

    async def _run(self, *, function, **kwargs):
        """保留线程操作，取消等待时不取消实际写入。"""
        self._operation = asyncio.create_task(coro=asyncio.to_thread(function, **kwargs))
        try:
            result = await asyncio.shield(arg=self._operation)
        except Exception:
            self._operation = None
            raise
        self._operation = None
        return result

    async def prepare(self) -> None:
        """启动时检查依赖并排他创建文件。

        :raises RecordingStartupError: 无法准备录制。
        """
        try:
            await self._run(function=self._prepare)
        except asyncio.CancelledError:
            await self.finish(stopped_at=0)
            raise
        logger.info(msg=f"视频录制已开启，目标文件：{self.path}")

    def _prepare(self) -> None:
        self._backend = load_video_backend()
        try:
            if self.settings.record_output is None:
                descriptor, path = tempfile.mkstemp(prefix="browscreen-recording-", suffix=".mp4")
                self.path = Path(path).resolve()
                self._file = os.fdopen(fd=descriptor, mode="wb")
            else:
                self.path = self.settings.record_output
                self._file = self.path.open(mode="xb")
            stat = os.fstat(self._file.fileno())
            self._file_identity = (stat.st_dev, stat.st_ino)
        except OSError as error:
            raise RecordingStartupError(f"无法创建录制文件 {self.path or self.settings.record_output}：{error}；请确认目录可写且文件不存在") from error

    def _record_error(self, *, message: str) -> None:
        self.error = self.error or message
        logger.error(msg=f"{message}，目标文件：{self.path}")

    async def append(self, *, frame: CurrentFrame, captured_at: float) -> None:
        """录入同一合成帧；录制失败时收尾并停止录制。

        :param frame: 已发布的完整 PNG 帧。
        :param captured_at: 本轮截图开始的单调时钟时间。
        """
        if self.closed:
            return
        try:
            await self._run(function=self._append, frame=frame, captured_at=captured_at)
        except Exception as error:
            self._record_error(message=f"视频录制写入失败：{error}")
            await self.finish(stopped_at=captured_at)

    def _append(self, *, frame: CurrentFrame, captured_at: float) -> None:
        with Image.open(fp=BytesIO(initial_bytes=frame.png_bytes)) as source:
            source.load()
            image = source.convert(mode="RGB")
        if self._stream is None:
            self._source_size = image.size
            self._canvas_size = (image.width + image.width % 2, image.height + image.height % 2)
            self._container = self._backend.open(file=self._file, mode="w", format="mp4")
            self._stream = self._container.add_stream(codec_name="libx264", rate=Fraction(1000, self.settings.interval_ms))
            self._stream.width, self._stream.height = self._canvas_size
            self._stream.pix_fmt = "yuv420p"
            self._stream.time_base = TIME_BASE
            self._stream.codec_context.time_base = TIME_BASE
            self._stream.codec_context.max_b_frames = 0
            self._stream.options = {"preset": "veryfast", "crf": "18"}
            self._epoch = captured_at
        canvas = Image.new(mode="RGB", size=self._canvas_size, color="black")
        if image.size == self._source_size:
            canvas.paste(im=image, box=(0, 0))
        else:
            image = ImageOps.contain(image=image, size=self._canvas_size, method=Image.Resampling.LANCZOS)
            canvas.paste(im=image, box=((canvas.width - image.width) // 2, (canvas.height - image.height) // 2))
        video_frame = self._backend.VideoFrame.from_image(img=canvas)
        video_frame.time_base = TIME_BASE
        pts = round((captured_at - self._epoch) / TIME_BASE)
        previous, self._pending = self._pending, None
        if previous is not None:
            pts = max(pts, previous.pts + 1)
            self._encode(frame=previous, duration=pts - previous.pts)
        video_frame.pts = pts
        self._pending = video_frame

    def _encode(self, *, frame, duration: int) -> None:
        frame.duration = duration
        self._durations[frame.pts] = duration
        for packet in self._stream.encode(frame=frame):
            self._mux(packet=packet)
        self.frame_count += 1

    def _mux(self, *, packet) -> None:
        # MP4 封装会换算时间基；先在编码器时间基中设置准确展示时长。
        pts = round(packet.pts * packet.time_base / TIME_BASE)
        duration = self._durations.pop(pts)
        packet.duration = max(1, round(duration * TIME_BASE / packet.time_base))
        self._container.mux(packets=packet)

    async def finish(self, *, stopped_at: float, reason: str | None = None) -> None:
        """等候在途操作并幂等收尾，保留首次停止时间。

        :param stopped_at: 采集停止的单调时钟时间。
        :param reason: 采集提前停止的原因。
        """
        if self._finish_task is None:
            self.stop_reason = reason
            self._finish_task = asyncio.create_task(coro=self._finish(stopped_at=stopped_at))
        await asyncio.shield(arg=self._finish_task)

    async def _finish(self, *, stopped_at: float) -> None:
        if self._operation is not None:
            try:
                await asyncio.shield(arg=self._operation)
            except Exception as error:
                self._record_error(message=f"视频录制写入失败：{error}")
            finally:
                self._operation = None
        await self._run(function=self._close, stopped_at=stopped_at)

    def _close(self, *, stopped_at: float) -> None:
        try:
            pending, self._pending = self._pending, None
            if pending is not None:
                end_pts = round((stopped_at - self._epoch) / TIME_BASE)
                self._encode(frame=pending, duration=max(1, end_pts - pending.pts))
            if self._stream is not None:
                for packet in self._stream.encode(frame=None):
                    self._mux(packet=packet)
                if self._durations:
                    raise RuntimeError("编码器未输出全部录制帧")
        except Exception as error:
            self._finalization_failed = True
            self._record_error(message=f"视频编码收尾失败：{error}")
        finally:
            try:
                if self._container is not None:
                    self._container.close()
            except Exception as error:
                self._finalization_failed = True
                self._record_error(message=f"MP4 文件关闭失败：{error}")
            finally:
                try:
                    if self._file is not None:
                        self._file.close()
                except Exception as error:
                    self._finalization_failed = True
                    self._record_error(message=f"录制文件关闭失败：{error}")
                try:
                    if self.path is not None and self.path.exists():
                        stat = self.path.stat()
                        if stat.st_size == 0 and (stat.st_dev, stat.st_ino) == self._file_identity:
                            self.path.unlink()
                        else:
                            self._has_output = True
                except OSError as error:
                    self._finalization_failed = True
                    self._record_error(message=f"录制文件清理失败：{error}")
                finally:
                    self.closed = True

    def report(self) -> None:
        """在服务结束时输出一次用户可获取的文件路径及结果。"""
        if self._reported:
            return
        self._reported = True
        if not self.closed:
            logger.error(msg=f"录制未完成收尾，残留文件可能无法播放：{self.path}")
        elif self.error:
            if not self._has_output:
                logger.error(msg=f"录制失败，未生成视频；原因：{self.error}")
            elif self._finalization_failed or self.frame_count == 0:
                logger.error(msg=f"录制失败，残留文件可能无法播放：{self.path}；原因：{self.error}")
            else:
                logger.error(msg=f"录制提前终止，已保存部分视频：{self.path}；原因：{self.error}")
        elif self.frame_count == 0:
            logger.info("未生成视频：未获取到有效截图")
        elif self.stop_reason:
            logger.warning(msg=f"录制提前终止，已保存部分视频：{self.path}；原因：{self.stop_reason}")
        else:
            logger.info(msg=f"录制完成，视频文件：{self.path}")
