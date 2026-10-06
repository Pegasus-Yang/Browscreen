# browscreen

[![Python](https://img.shields.io/badge/Python-3.14%2B-blue.svg)](https://github.com/Pegasus-Yang/Browscreen/blob/main/pyproject.toml)
[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/Pegasus-Yang/Browscreen/blob/main/LICENSE)

简体中文 | [English](https://github.com/Pegasus-Yang/Browscreen/blob/main/README.en.md)

浏览器画面与鼠标指针预览服务。browscreen（browser＋screen）通过 Chrome DevTools Protocol（CDP）连接已有 Chrome，将当前视口截图和外部提供的指针坐标合成 PNG，用于网页预览、图片接口和 webhook 推送。

## 使用效果

下图展示 [DSH-Test-Plugin](https://github.com/Pegasus-Yang/DSH-Test-Plugin) 接入 browscreen 的使用效果：在测试执行过程中，通过悬浮窗观察目标浏览器的实时画面和指针，体验类似 Codex 展示浏览器操作的悬浮预览。

![DSH-Test-Plugin 中的浏览器实时画面悬浮窗，旁边保留对话记录和测试步骤进度](https://raw.githubusercontent.com/Pegasus-Yang/Browscreen/main/doc/user-guide/images/%E6%B5%8F%E8%A7%88%E5%99%A8%E5%AE%9E%E6%97%B6%E7%94%BB%E9%9D%A2.jpg)

图中的聊天、测试步骤和悬浮窗控件由 DSH-Test-Plugin 提供；浏览器操作由宿主驱动，browscreen 负责画面采集与指针合成，预览保持只读。自己的网页也可以通过 `iframe` 嵌入预览页，并加入拖动、缩放、放大／还原和关闭控件，参考[页面悬浮窗示例](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/user-guide/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.md#%E9%A1%B5%E9%9D%A2%E6%82%AC%E6%B5%AE%E7%AA%97%E7%A4%BA%E4%BE%8B)。

## 功能

- 默认每 300 毫秒采集当前视口，网页、图片接口和 webhook 共用最新帧。
- 读取工作目录的 `.cdp` 连接浏览器，读取 `.mouse` 在 CSS 视口坐标上合成指针。
- 浏览器失效后清空旧图，在限定时间内重读端点并恢复采集。
- 正常退出时清空已有 `.mouse`，保留 `.cdp` 和外部浏览器。
- 可选 MP4 视频录制，复用含指针的截图，正常结束后输出文件路径。

服务采用 Python 3.14、FastAPI 和 Pydantic v2，一个进程运行一个采集循环。浏览器启动、页面导航和环境隔离由调用方管理；预览页只读，不转发鼠标或键盘操作。

## 安装

准备 `uv` 和 Python 3.14，从 GitHub 获取源码后安装：

```sh
git clone https://github.com/Pegasus-Yang/Browscreen.git
cd Browscreen
uv sync --locked --no-dev \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
.venv/bin/browscreen version
```

需要安装 Python 时先执行 `uv python install 3.14`。当前安装方式为源码或自行构建的 wheel；详见[安装与运行](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/deployment/%E5%AE%89%E8%A3%85%E4%B8%8E%E8%BF%90%E8%A1%8C.md)。

## 快速开始

准备一个已有工作目录，以及启用了远程调试、包含打开页面的 Chrome。将实际调试地址写入 `.cdp`：

```sh
work_dir="/absolute/path/to/workspace"
printf '%s\n' 'http://127.0.0.1:9222' > "$work_dir/.cdp"
uv run --no-sync browscreen --work-dir "$work_dir"
```

打开 `http://127.0.0.1:8000/` 查看画面。另一个终端向同一目录的 `.mouse` 写入 `320,180`，即可在下一个新帧显示指针。工作目录和 Chrome 由外部系统准备，示例路径和端口需按实际环境替换。

`.cdp` 尚未可用时 HTTP 保持可访问，默认等待 60 秒，等待预算持续到首个有效 PNG 生成。超时后修正端点并重启服务。

## 可选视频录制

基础安装默认不录制，也不安装视频依赖。需要录制时先安装 `video` extra，再显式开启：

```sh
uv sync --locked --no-dev --extra video \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
uv run --no-sync browscreen --work-dir /absolute/path/to/workspace --record
```

默认在系统临时目录生成唯一的 MP4，正常退出后输出绝对路径并保留文件。可追加 `--record-output /absolute/path/to/recordings/session.mp4` 指定文件；父目录须已存在，已有文件不会被覆盖。开启录制但缺少依赖时，服务会提示安装 `browscreen[video]` 并以退出码 `1` 结束。

录制保留实际采样时间；编码耗时可能降低采集频率。通过 SIGINT 或 SIGTERM 正常停止并等待退出，才能完成视频收尾。时间轴、尺寸变化和失败行为见[使用说明](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/user-guide/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.md#%E5%8F%AF%E9%80%89%E8%A7%86%E9%A2%91%E5%BD%95%E5%88%B6)。

## 命令行

安装后提供一个 `browscreen` 命令。在源码安装环境中，可通过 `.venv/bin/browscreen` 或 `uv run --no-sync browscreen` 调用。

| 命令 | 用途 |
| --- | --- |
| `browscreen --help` | 查看全部命令与参数 |
| `browscreen version` / `browscreen --version` | 查询安装版本并退出，无需工作目录 |
| `browscreen --work-dir <目录>` | 启动服务，默认 INFO 日志 |
| `browscreen -v --work-dir <目录>` | 启动服务并开启 DEBUG 和 HTTP 访问日志 |

默认省略网页轮询、HTTPX 请求和重复重试细节；状态变化、超时及异常仍会记录。同一 webhook 连续失败仅首次告警，恢复后记录一次；每帧仍按原规则发送。完整参数与日志说明见[命令行参考](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/reference/%E5%91%BD%E4%BB%A4%E8%A1%8C.md)。

## HTTP 接口

| 接口 | 用途 |
| --- | --- |
| `GET /` | 只读网页预览 |
| `GET /api/screenshot` | 最新合成 PNG，附带帧编号、UTC 采集开始时间和 `no-store` |
| `POST /api/webhooks` | 注册 HTTP(S) 接收地址，向后续新帧发送 PNG |

截图与发送顺序执行，每帧向所有接收地址并发尝试一次。慢 webhook 会降低采集频率，每次发送最多等待 3 秒；失败不自动重试、不跟随重定向。注册保留至进程退出。协议与错误码见[使用说明](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/user-guide/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.md)。

## 文档

- [安装与运行](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/deployment/%E5%AE%89%E8%A3%85%E4%B8%8E%E8%BF%90%E8%A1%8C.md)：环境、安装、启动、退出和构建。
- [命令行参考](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/reference/%E5%91%BD%E4%BB%A4%E8%A1%8C.md)：命令、参数和日志级别。
- [使用说明](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/user-guide/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.md)：文件协议、预览、图片和 webhook。
- [页面悬浮窗示例](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/user-guide/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.md#%E9%A1%B5%E9%9D%A2%E6%82%AC%E6%B5%AE%E7%AA%97%E7%A4%BA%E4%BE%8B)：复制代码显示实时画面，支持拖动、调整大小、放大／还原和关闭。
- [文档导航](https://github.com/Pegasus-Yang/Browscreen/blob/main/doc/README.md)：完整阅读路线和技术设计。
- [版本变动历史](https://github.com/Pegasus-Yang/Browscreen/blob/main/changelog.md)：各版本的新增、变更和修复。

## 开发与贡献

开发环境需要 Node.js 22 或更高版本，以运行预览脚本回归，无需 npm 依赖：

```sh
uv sync --locked --group dev \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
.venv/bin/python -m pytest -q -W error
```

默认测试使用模拟浏览器端点；真实 Chrome 验证需要另行执行。缺少 Node.js 时预览测试会跳过，不代表前端验证通过。

验证录制功能时，在同步命令中增加 `--extra video`；缺少该依赖时真实视频测试会跳过，不能据此确认录制功能通过。

问题和建议请提交到 [GitHub Issues](https://github.com/Pegasus-Yang/Browscreen/issues)。提交改动前请阅读[贡献指南](https://github.com/Pegasus-Yang/Browscreen/blob/main/CONTRIBUTING.md)。项目由 [Pegasus-Yang](https://github.com/Pegasus-Yang) 维护。

## 许可证

项目采用 [MIT 许可证](https://github.com/Pegasus-Yang/Browscreen/blob/main/LICENSE)。
