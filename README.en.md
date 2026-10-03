# browscreen

[简体中文](README.md) | English

browscreen (browser + screen) is a browser screenshot and mouse pointer preview service. It connects to an existing Chrome instance through the Chrome DevTools Protocol (CDP), captures the current viewport every 300 ms by default, and overlays the pointer position supplied in a `.mouse` file. The web preview, screenshot API, and webhooks share the same composed PNG for each frame.

The service uses Python 3.14, FastAPI, and Pydantic v2. A single process owns one capture loop and one latest-frame cache. The host application manages browser startup, navigation, shutdown, and environment isolation; browscreen handles capture, composition, and delivery.

## Quick start

You need `uv`, Python 3.14, and an existing Chrome instance with a reachable CDP endpoint and an open page. Run these commands from the repository root to install the project and development dependencies:

```sh
uv sync --locked --group dev \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
```

Choose an existing working directory and write your Chrome debugging address to its `.cdp` file. Replace both values below with your actual directory and endpoint:

```sh
work_dir="/absolute/path/to/workspace"
printf '%s\n' 'http://127.0.0.1:9222' > "$work_dir/.cdp"
uv run --no-sync browscreen --work-dir "$work_dir"
```

Open `http://127.0.0.1:8000/` to view the browser. From another terminal, write `320,180` to the same working directory's `.mouse` file to display a pointer at that position in CSS viewport pixels.

If the browser is not yet available, HTTP remains accessible while the service waits. The default 60-second budget covers connection and creation of the first valid frame. After this budget expires, correct the endpoint and restart browscreen.

## Interfaces

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Read-only web preview |
| `GET /api/screenshot` | Latest composed PNG, with frame ID, UTC capture start time, and `Cache-Control: no-store` |
| `POST /api/webhooks` | Register an HTTP(S) receiver for subsequent frames |

The preview does not forward mouse or keyboard input to Chrome. It resumes polling after restoration from the browser's back/forward cache, cancels a stalled preview operation after five seconds, and skips reloading an already displayed frame.

For each frame, all registered webhook receivers are contacted concurrently. The next capture waits for those delivery attempts to finish, so slow receivers reduce the capture rate. Each delivery has a three-second total timeout. Failed deliveries are logged without automatic retries or redirect following.

On a browser failure, browscreen clears the old image, rereads `.cdp`, and attempts recovery within a new budget. Repeated failures before a valid recovery frame do not reset that budget. Webhook registrations survive reconnection and remain in memory until the service exits. An unexpected capture-task failure returns `capture_failed` and requires checking the logs before restarting.

Normal shutdown, including graceful SIGINT or SIGTERM handling, clears the contents of an existing `.mouse` file. It preserves the file itself, `.cdp`, and the external Chrome instance.

## Documentation

- [Installation and deployment](doc/deployment/安装与运行.en.md): prerequisites, installation, startup options, lifecycle, troubleshooting, and building.
- [User guide](doc/user-guide/使用说明.en.md): endpoint and pointer files, preview behavior, screenshot responses, and webhook integration.
- [Documentation index](doc/README.md): the complete project documentation, including Chinese design and validation records.

These English documents describe the current service. The preview interface, CLI descriptions, and runtime messages are currently in Simplified Chinese. API clients can use the stable error `code` values documented in the user guide.

## Tests

Use the repository's virtual environment:

```sh
.venv/bin/python -m pytest -q -W error
```

The full suite also requires Node.js 22 or later to run preview-script tests through pytest; no npm dependencies are needed. Without Node.js, that test is explicitly skipped, so the result does not confirm frontend coverage. The default test suite uses simulated browser endpoints and does not launch a real Chrome instance. Real-browser verification is a separate, explicitly invoked procedure.
