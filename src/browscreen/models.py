"""启动配置、文件数据与 HTTP 数据契约。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, HttpUrl, field_validator, model_validator


class Settings(BaseModel):
    """校验实例的工作目录与启动参数。"""

    model_config = ConfigDict(frozen=True)

    work_dir: Path
    adapter: Literal["chrome-cdp"] = "chrome-cdp"
    interval_ms: int = Field(default=300, gt=0)
    connect_wait_timeout_s: float = Field(default=60, gt=0, allow_inf_nan=False)
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    verbose: bool = False
    record: bool = False
    record_output: Path | None = None

    @field_validator("work_dir", mode="before")
    @classmethod
    def validate_work_dir(cls, value: str | Path) -> Path:
        """将已有工作目录转换为绝对路径。

        :raises ValueError: 路径不存在或不是目录。
        """
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"工作目录不存在或不是目录：{path}")
        return path

    @field_validator("record_output", mode="before")
    @classmethod
    def validate_record_output(cls, value: str | Path | None) -> Path | None:
        """解析 MP4 文件路径，要求父目录已经存在。"""
        if value is None:
            return None
        path = Path(value).expanduser().resolve()
        if path.is_dir() or path.suffix.lower() != ".mp4":
            raise ValueError("--record-output 必须是完整的 .mp4 文件路径")
        if not path.parent.is_dir():
            raise ValueError(f"录制文件的父目录不存在：{path.parent}")
        return path

    @model_validator(mode="after")
    def validate_record_switch(self) -> "Settings":
        """输出位置不能隐式开启录制。"""
        if self.record_output is not None and not self.record:
            raise ValueError("使用 --record-output 时必须同时提供 --record")
        return self


class MousePosition(BaseModel):
    """以当前 CSS 视口左上角为原点的有限坐标。"""

    model_config = ConfigDict(frozen=True)
    x: FiniteFloat
    y: FiniteFloat


class WebhookRegistration(BaseModel):
    """注册接收后续 PNG 新帧的 HTTP(S) 地址。"""

    url: HttpUrl


class ErrorResponse(BaseModel):
    """截图不可用时的公共错误响应。"""

    code: Literal["waiting_for_browser", "screenshot_not_ready", "browser_wait_timeout", "capture_failed"]
    message: str


@dataclass(frozen=True, slots=True)
class CurrentFrame:
    """一次性发布的完整合成帧，避免图片与元数据交叉。"""

    frame_id: int
    capture_started_at: str
    png_bytes: bytes

    @property
    def headers(self) -> dict[str, str]:
        """返回图片接口和 webhook 共用的帧元数据。"""
        return {
            "X-Frame-Id": str(self.frame_id),
            "X-Capture-Started-At": self.capture_started_at,
        }
