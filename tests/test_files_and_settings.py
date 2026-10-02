"""工程入口、配置和固定文件协议验证。"""

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from browscreen.files import clear_mouse, read_endpoint, read_mouse
from browscreen.main import parse_settings
from browscreen.models import Settings


def test_settings_defaults_and_cli(tmp_path):
    settings = parse_settings(argv=["--work-dir", str(tmp_path)])
    assert settings.work_dir == tmp_path.resolve()
    assert settings.adapter == "chrome-cdp"
    assert settings.interval_ms == 300
    assert settings.connect_wait_timeout_s == 60
    assert parse_settings(argv=["--work-dir", str(tmp_path), "--adapter", "chrome-cdp"]) == settings
    result = subprocess.run(args=[str(Path(sys.executable).parent / "browscreen"), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "--work-dir" in result.stdout


@pytest.mark.parametrize("updates", [
    {"interval_ms": 0}, {"interval_ms": -1}, {"connect_wait_timeout_s": 0},
    {"connect_wait_timeout_s": float("inf")}, {"port": 0}, {"port": 65536}, {"adapter": "unknown"},
])
def test_invalid_settings(tmp_path, updates):
    with pytest.raises(ValidationError):
        Settings(work_dir=tmp_path, **updates)


def test_work_dir_must_exist_and_be_directory(tmp_path):
    for path in [tmp_path / "missing", tmp_path / "file"]:
        if path.name == "file":
            path.write_text("data")
        with pytest.raises(ValidationError):
            Settings(work_dir=path)


@pytest.mark.parametrize("content,expected", [
    (b"", None), (b" \n", None), (b"\xff", None),
    (b"http://localhost:9222\nhttp://localhost:9223", None),
    (b"\xef\xbb\xbf http://localhost:9222 \n", "http://localhost:9222"),
])
def test_endpoint_text(tmp_path, content, expected):
    path = tmp_path / ".cdp"
    path.write_bytes(content)
    assert read_endpoint(path=path) == expected


@pytest.mark.parametrize("content", [b"", b"\xff", b"abc", b"1,2,3", b"NaN,180", b"1,Infinity", b"1,", b"true,1"])
def test_invalid_mouse_does_not_reuse_previous_value(tmp_path, content):
    path = tmp_path / ".mouse"
    path.write_text("320,180")
    assert read_mouse(path=path).x == 320
    path.write_bytes(content)
    assert read_mouse(path=path) is None
    path.unlink()
    assert read_mouse(path=path) is None


def test_mouse_decimals_whitespace_and_cleanup(tmp_path):
    mouse = tmp_path / ".mouse"
    endpoint = tmp_path / ".cdp"
    endpoint.write_text("http://localhost:9222")
    mouse.write_text(" 320.5, 180.0 \n")
    position = read_mouse(path=mouse)
    assert (position.x, position.y) == (320.5, 180)
    clear_mouse(path=mouse)
    assert mouse.exists() and mouse.stat().st_size == 0
    assert endpoint.read_text() == "http://localhost:9222"
    mouse.unlink()
    clear_mouse(path=mouse)
    assert not mouse.exists()
