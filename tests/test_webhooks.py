"""真实本地 HTTP 接收器验证逐帧字节与慢接收策略。"""

import asyncio
import hashlib
import logging
from io import BytesIO

import httpx
import pytest
from PIL import Image

import browscreen.webhooks as webhooks_module
from browscreen.app import create_app
from browscreen.models import CurrentFrame, Settings
from browscreen.webhooks import push_frame


async def receiver(*, records, delay=0, status=204):
    async def handle(reader, writer):
        try:
            while True:
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header.decode().split("\r\n")
                headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
                headers = {key.lower(): value for key, value in headers.items()}
                body = await reader.readexactly(int(headers["content-length"]))
                records.append({"headers": headers, "body": body, "at": asyncio.get_running_loop().time()})
                await asyncio.sleep(delay=delay)
                writer.write(f"HTTP/1.1 {status} Result\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n".encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.start_server(client_connected_cb=handle, host="127.0.0.1", port=0)


async def test_registration_normalizes_and_rejects_invalid_urls(tmp_path, fake_adapter):
    app = create_app(settings=Settings(work_dir=tmp_path), adapter=fake_adapter)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as client:
            first = await client.post("/api/webhooks", json={"url": "HTTP://EXAMPLE.COM:80"})
            repeated = await client.post("/api/webhooks", json={"url": "http://example.com/"})
            assert first.status_code == 201 and repeated.status_code == 200
            assert first.json() == {"url": "http://example.com/", "registered": True}
            assert app.state.capture.webhooks == {"http://example.com/"}
            for url in ["ftp://example.com", "invalid", "", "ws://example.com"]:
                assert (await client.post("/api/webhooks", json={"url": url})).status_code == 422


async def test_each_new_frame_shared_bytes_and_slow_receiver(tmp_path, fake_adapter, wait_for):
    (tmp_path / ".browser").write_text("first")
    (tmp_path / ".mouse").write_text("32,18")
    fast, slow = [], []
    fast_server = await receiver(records=fast)
    slow_server = await receiver(records=slow, delay=0.04)
    servers = [fast_server, slow_server]
    urls = [f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/frames" for server in servers]
    app = create_app(settings=Settings(work_dir=tmp_path, interval_ms=10), adapter=fake_adapter)
    async with fast_server, slow_server:
        async with app.router.lifespan_context(app):
            service = app.state.capture
            await wait_for(lambda: service.current_frame is not None)
            historical_id = service.frame_id
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://preview") as client:
                for url in urls:
                    assert (await client.post("/api/webhooks", json={"url": url})).status_code == 201
                assert (await client.post("/api/webhooks", json={"url": urls[0]})).status_code == 200
                await wait_for(lambda: len(fast) >= 3 and len(slow) >= 3)
                response = await client.get("/api/screenshot")
                records = {int(item["headers"]["x-frame-id"]): item for item in fast}
                delivered = records[int(response.headers["x-frame-id"])]
                assert hashlib.sha256(delivered["body"]).digest() == hashlib.sha256(response.content).digest()
                assert delivered["headers"]["x-capture-started-at"] == response.headers["x-capture-started-at"]
                for bucket in [fast, slow]:
                    ids = [int(item["headers"]["x-frame-id"]) for item in bucket]
                    assert ids == list(range(ids[0], ids[0] + len(ids)))
                    assert ids[0] > historical_id
                    assert len(ids) == len(set(ids))
                    assert all(item["headers"]["content-type"] == "image/png" for item in bucket)
                    assert Image.open(BytesIO(bucket[0]["body"])).getpixel((32, 18))[:3] == (0, 0, 0)
                assert [item["body"] for item in fast] == [item["body"] for item in slow]
                assert all(b["at"] - a["at"] >= 0.035 for a, b in zip(slow, slow[1:]))
                assert fake_adapter.captures == service.frame_id


@pytest.mark.parametrize("failure", ["http_error", "connection_error", "timeout", "redirect"])
async def test_send_failures_are_independent_and_bounded(failure, png_bytes, monkeypatch, caplog):
    calls = []
    assert webhooks_module.WEBHOOK_TIMEOUT_S == 3
    monkeypatch.setattr(webhooks_module, "WEBHOOK_TIMEOUT_S", 0.03)

    async def handle(request):
        calls.append(request.url.host)
        if request.url.host == "good":
            return httpx.Response(status_code=204)
        if failure == "connection_error":
            raise httpx.ConnectError(message="refused", request=request)
        if failure == "timeout":
            await asyncio.sleep(delay=1)
        if failure == "redirect":
            return httpx.Response(status_code=302, headers={"location": "http://good/redirected"})
        return httpx.Response(status_code=500)

    frame = CurrentFrame(frame_id=1, capture_started_at="2026-10-02T00:00:00Z", png_bytes=png_bytes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler=handle), follow_redirects=True) as client:
        started = asyncio.get_running_loop().time()
        await push_frame(client=client, urls=("http://bad/", "http://good/"), frame=frame, failed_urls=set())
        assert asyncio.get_running_loop().time() - started < 0.1
    assert sorted(calls) == ["bad", "good"]
    assert "webhook 发送失败" in caplog.text


async def test_webhook_logs_failures_once_then_reports_recovery(png_bytes, caplog):
    caplog.set_level(level=logging.DEBUG, logger="browscreen.webhooks")
    status, calls = 500, []

    def handle(request):
        calls.append(request.url.host)
        return httpx.Response(status_code=status if request.url.host == "bad" else 204)

    failed_urls = set()
    frame = CurrentFrame(frame_id=1, capture_started_at="2026-10-02T00:00:00Z", png_bytes=png_bytes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler=handle)) as client:
        for _ in range(3):
            await push_frame(client=client, urls=("http://bad/", "http://good/"), frame=frame, failed_urls=failed_urls)
        assert failed_urls == {"http://bad/"}
        status = 204
        await push_frame(client=client, urls=("http://bad/", "http://good/"), frame=frame, failed_urls=failed_urls)
        assert failed_urls == set()
        status = 500
        await push_frame(client=client, urls=("http://bad/", "http://good/"), frame=frame, failed_urls=failed_urls)
    records = [record for record in caplog.records if record.name == "browscreen.webhooks"]
    assert sum(record.levelno == logging.WARNING for record in records) == 2
    assert sum(record.levelno == logging.DEBUG for record in records) == 2
    assert sum("webhook 发送已恢复" in record.message for record in records) == 1
    assert calls.count("bad") == calls.count("good") == 5
