"""命令行参数与单进程服务入口。"""

import argparse
import logging

import uvicorn
from pydantic import ValidationError

from browscreen.app import create_app
from browscreen.models import Settings


def parse_settings(*, argv: list[str] | None = None) -> Settings:
    """解析并校验命令行配置。

    :param argv: 参数列表；None 表示读取进程参数。
    :returns: 已校验的实例配置。
    """
    parser = argparse.ArgumentParser(description="browscreen 浏览器画面与鼠标指针预览")
    parser.add_argument("--work-dir", required=True, help="已有的文件交换工作目录")
    parser.add_argument("--adapter", default="chrome-cdp", help="浏览器适配器（chrome-cdp）")
    parser.add_argument("--interval-ms", type=int, default=300, help="截图与端点重试间隔（毫秒）")
    parser.add_argument("--connect-wait-timeout-s", type=float, default=60, help="每轮连接等待上限（秒）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args(args=argv)
    try:
        return Settings(**vars(arguments))
    except ValidationError as error:
        parser.error(message=str(error))


def main() -> None:
    """启动一个 worker 和一个采集循环。"""
    settings = parse_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app=create_app(settings=settings), host=settings.host, port=settings.port, workers=1)
