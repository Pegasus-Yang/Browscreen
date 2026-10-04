"""已安装命令、版本来源与日志开关回归。"""

import logging
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

import browscreen.main as cli_module
from browscreen import __version__
from browscreen.app import create_app
from browscreen.main import parse_settings
from browscreen.models import Settings


@pytest.mark.parametrize("arguments", [["version"], ["--version"], ["version", "-v"]])
def test_installed_version_command_without_work_dir(tmp_path, arguments):
    result = subprocess.run(args=[str(Path(sys.executable).parent / "browscreen"), *arguments],
                            cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == f"browscreen {version(distribution_name='browscreen')}"
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_api_and_cli_share_installed_version(tmp_path):
    app = create_app(settings=Settings(work_dir=tmp_path))
    assert app.openapi()["info"]["version"] == __version__ == version(distribution_name="browscreen")


@pytest.mark.parametrize("flag", ["-v", "--verbose"])
def test_verbose_flag_before_or_after_work_dir(tmp_path, flag):
    assert parse_settings(argv=[flag, "--work-dir", str(tmp_path)]).verbose
    assert parse_settings(argv=["--work-dir", str(tmp_path), flag]).verbose
    assert not parse_settings(argv=["--work-dir", str(tmp_path)]).verbose


@pytest.mark.parametrize("arguments", [[], ["-v"], ["unknown"]])
def test_service_requires_work_dir_and_rejects_unknown_commands(arguments):
    with pytest.raises(SystemExit) as error:
        parse_settings(argv=arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("verbose", [False, True])
def test_main_controls_server_and_access_logging(tmp_path, monkeypatch, verbose):
    arguments = ["browscreen", "--work-dir", str(tmp_path)] + (["-v"] if verbose else [])
    monkeypatch.setattr(sys, "argv", arguments)
    logging_options, server_options = {}, {}
    monkeypatch.setattr(cli_module, "configure_logging", lambda **kwargs: logging_options.update(kwargs))
    monkeypatch.setattr(cli_module.uvicorn, "run", lambda **kwargs: server_options.update(kwargs))
    cli_module.main()
    assert logging_options == {"verbose": verbose}
    assert server_options["log_level"] == ("debug" if verbose else "info")
    assert server_options["access_log"] is verbose
    assert server_options["workers"] == 1


@pytest.mark.parametrize("verbose", [False, True])
def test_third_party_request_logs_follow_verbose_flag(monkeypatch, request, verbose):
    options = {}
    monkeypatch.setattr(cli_module.logging, "basicConfig", lambda **kwargs: options.update(kwargs))
    for name in ("httpx", "httpcore", "websockets", "PIL"):
        logger = logging.getLogger(name=name)
        request.addfinalizer(lambda logger=logger, level=logger.level: logger.setLevel(level=level))
    cli_module.configure_logging(verbose=verbose)
    assert options["level"] == (logging.DEBUG if verbose else logging.INFO)
    for name in ("httpx", "httpcore", "websockets", "PIL"):
        logger = logging.getLogger(name=name)
        assert logger.isEnabledFor(level=logging.INFO) is verbose
        assert logger.isEnabledFor(level=logging.DEBUG) is verbose
        assert logger.isEnabledFor(level=logging.WARNING)
