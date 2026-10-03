# browscreen

简体中文 | [English](README.en.md)

browscreen（browser＋screen）是浏览器画面与鼠标指针预览服务。它通过浏览器适配器连接已有浏览器，默认每 300 毫秒截图，读取工作目录中的 `.mouse` 合成指针；网页、图片接口和 webhook 共用同一张 PNG。当前支持 Chrome＋CDP，浏览器和并行环境由主服务管理。

服务采用 Python 3.14、FastAPI 和 Pydantic v2，一个进程、一个采集循环和一个最新帧缓存。适配器负责端点发现与截图，公共流程负责调度、合成和输出。

## 快速开始

在项目根目录安装：

```sh
uv sync --locked --group dev \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
```

外部系统准备已有工作目录，将 Chrome 调试地址（例如 `http://127.0.0.1:9222`）写入其中的 `.cdp`，然后启动：

```sh
uv run --no-sync browscreen --work-dir /absolute/path/to/workspace
```

打开 `http://127.0.0.1:8000/`。向该目录的 `.mouse` 写入 `320,180` 即可显示指针。`.cdp` 尚未可用时，服务保持 HTTP 可访问，默认等待 60 秒；超时后修正端点并重启。

## 接口与运行约定

| 接口 | 用途 |
| --- | --- |
| `GET /` | 只读网页预览 |
| `GET /api/screenshot` | 最新合成 PNG，附带帧编号、UTC 采集开始时间和 `no-store` |
| `POST /api/webhooks` | 规范化注册 HTTP(S) 地址，向后续新帧逐地址发送 PNG |

截图与发送顺序执行。慢 webhook 会降低采集频率，每帧仍尝试推送，发送最长 3 秒。连接失效后清空旧图、重新读取端点并恢复；注册保留至进程结束。正常停止或 SIGINT／SIGTERM 优雅退出时，已有 `.mouse` 内容清空，`.cdp` 和外部 Chrome 保留。

每轮连接等待持续到首个有效帧生成，连续截图失败会按间隔重试并在预算耗尽后停止。预览支持历史缓存恢复、5 秒读取超时和重复帧跳过加载；采集任务异常停止时返回 `capture_failed`，提示检查日志后重启。

## 文档与验证

- [安装与运行](doc/deployment/安装与运行.md)：环境、参数、启动和构建。
- [使用说明](doc/user-guide/使用说明.md)：文件协议、图片和 webhook。
- [设计方案](doc/design/设计方案.md)：适配器边界、状态和几何契约。
- [实施方案](doc/design/具体实施方案.md)：实施步骤和验证方法。

完整导航见[doc/README.md](doc/README.md)。运行自动验证：

完整验证需同时具备 Node.js 22 或更高版本；预览回归由 pytest 调用 Node 内置测试运行器，无 npm 依赖。

```sh
.venv/bin/python -m pytest -q -W error
```
