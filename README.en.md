# browscreen

[![Python](https://img.shields.io/badge/Python-3.14%2B-blue.svg)](pyproject.toml)
[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[简体中文](README.md) | English

A browser screenshot and mouse pointer preview service. browscreen (browser + screen) connects to an existing Chrome instance through the Chrome DevTools Protocol (CDP), combines viewport screenshots with externally supplied pointer coordinates, and shares each PNG through a web preview, screenshot API, and webhooks.

## Features

- Capture the current viewport every 300 ms by default, with one shared latest frame.
- Read the browser endpoint from `.cdp` and overlay CSS viewport coordinates from `.mouse`.
- Clear stale images and recover browser connections within a bounded waiting budget.
- Clear an existing `.mouse` during graceful shutdown, preserving `.cdp` and the external browser.

The service uses Python 3.14, FastAPI, and Pydantic v2, with one capture loop per process. The host application manages browser startup, navigation, and environment isolation. The read-only preview does not forward mouse or keyboard input.

## Installation

With `uv` and Python 3.14 available, clone and install the source:

```sh
git clone https://github.com/Pegasus-Yang/Browscreen.git
cd Browscreen
uv sync --locked --no-dev \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
.venv/bin/browscreen version
```

If needed, install Python first with `uv python install 3.14`. Install from source or a locally built wheel; see [installation and deployment](doc/deployment/安装与运行.en.md) for details.

## Quick start

Prepare an existing working directory and a Chrome instance with remote debugging enabled and an open page. Write its actual debugging address to `.cdp`:

```sh
work_dir="/absolute/path/to/workspace"
printf '%s\n' 'http://127.0.0.1:9222' > "$work_dir/.cdp"
uv run --no-sync browscreen --work-dir "$work_dir"
```

Open `http://127.0.0.1:8000/`. From another terminal, write `320,180` to the same directory's `.mouse` file to display the pointer in the next frame. Replace the example path and ports with those managed by your application.

HTTP stays available while the browser is unavailable. The default 60-second startup budget includes connection and creation of the first valid PNG. After timeout, correct the endpoint and restart the service.

## Command line

Installation provides one `browscreen` command. For a source installation, use `.venv/bin/browscreen` or `uv run --no-sync browscreen`.

| Command | Purpose |
| --- | --- |
| `browscreen --help` | Show all commands and options |
| `browscreen version` / `browscreen --version` | Print the installed version and exit; no working directory required |
| `browscreen --work-dir <directory>` | Start the service with INFO logging |
| `browscreen -v --work-dir <directory>` | Start with DEBUG and HTTP access logs |

Default logs omit preview polling, HTTPX requests, and repeated retry details. State changes, timeouts, and exceptions remain visible. A webhook's consecutive failures produce one warning followed by a recovery message; delivery is still attempted for every frame. See the [command-line reference](doc/reference/命令行.en.md) for all options and logging behavior.

## HTTP interfaces

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Read-only web preview |
| `GET /api/screenshot` | Latest composed PNG, with frame ID, UTC capture start time, and `no-store` |
| `POST /api/webhooks` | Register an HTTP(S) receiver for subsequent frames |

Each capture waits for concurrent delivery attempts to all registered receivers. Slow receivers reduce the capture rate. Each delivery has a three-second total timeout, with no automatic retries or redirect following. Registrations last until process exit. See the [user guide](doc/user-guide/使用说明.en.md) for protocols and error codes.

## Documentation

- [Installation and deployment](doc/deployment/安装与运行.en.md): prerequisites, installation, startup, shutdown, and building.
- [Command-line reference](doc/reference/命令行.en.md): commands, options, and logging levels.
- [User guide](doc/user-guide/使用说明.en.md): exchange files, preview, screenshots, and webhooks.
- [Documentation index](doc/README.md): reading routes and Chinese technical design documents.

The preview UI, CLI help, and runtime messages are currently in Simplified Chinese. Use the stable API error `code` values for programmatic handling.

## Development and contributions

The full suite requires Node.js 22 or later for the preview-script tests; no npm dependencies are needed:

```sh
uv sync --locked --group dev \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
.venv/bin/python -m pytest -q -W error
```

Standard tests use simulated browser endpoints. Real-Chrome verification is a separate procedure. Without Node.js, the preview test is explicitly skipped, so the result does not confirm frontend coverage.

Report bugs and suggestions through [GitHub Issues](https://github.com/Pegasus-Yang/Browscreen/issues). Read the [contribution guide](CONTRIBUTING.md) before submitting changes. Maintained by [Pegasus-Yang](https://github.com/Pegasus-Yang).

## License

Licensed under the [MIT License](LICENSE).
