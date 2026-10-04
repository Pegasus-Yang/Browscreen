"""命令行参数与单进程服务入口。"""

import argparse
import logging

import uvicorn
from pydantic import ValidationError

from browscreen import __version__
from browscreen.app import create_app
from browscreen.models import Settings


def parse_settings(*, argv: list[str] | None = None) -> Settings:
    """解析并校验命令行配置。

    :param argv: 参数列表；None 表示读取进程参数。
    :returns: 已校验的实例配置。
    """
    parser = argparse.ArgumentParser(description="browscreen 浏览器画面与鼠标指针预览")
    parser.add_argument("command", nargs="?", choices=["version"], help="查询版本；省略时启动服务")
    parser.add_argument("--version", action="version", version=f"browscreen {__version__}", help="显示版本并退出")
    parser.add_argument("-v", "--verbose", action="store_true", help="开启 DEBUG 日志和 HTTP 访问日志")
    parser.add_argument("--work-dir", help="启动服务必填：已有的文件交换工作目录")
    parser.add_argument("--adapter", default="chrome-cdp", help="浏览器适配器（chrome-cdp）")
    parser.add_argument("--interval-ms", type=int, default=300, help="截图与端点重试间隔（毫秒）")
    parser.add_argument("--connect-wait-timeout-s", type=float, default=60, help="每轮等待首个有效帧的上限（秒）")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 端口（默认 8000）")
    arguments = parser.parse_args(args=argv)
    if arguments.command == "version":
        print(f"browscreen {__version__}")
        parser.exit()
    if arguments.work_dir is None:
        parser.error(message="启动服务必须提供 --work-dir")
    del arguments.command
    try:
        return Settings(**vars(arguments))
    except ValidationError as error:
        parser.error(message=str(error))


def configure_logging(*, verbose: bool) -> None:
    """配置服务日志，默认省略第三方库的逐请求日志。

    :param verbose: 是否开启 DEBUG 诊断日志。
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in ("httpx", "httpcore", "websockets", "PIL"):
        logging.getLogger(name=name).setLevel(level=level if verbose else logging.WARNING)


def main() -> None:
    """处理命令，或启动一个 worker 和一个采集循环。"""
    settings = parse_settings()
    configure_logging(verbose=settings.verbose)
    uvicorn.run(app=create_app(settings=settings), host=settings.host, port=settings.port, workers=1,
                log_level="debug" if settings.verbose else "info", access_log=settings.verbose)
