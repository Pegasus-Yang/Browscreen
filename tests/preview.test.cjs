// 执行真实预览脚本，以受控网络、图片加载和时间验证生命周期竞态。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../src/browscreen/preview.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('__INTERVAL_MS__', '300');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const listeners = new Map();
  const timers = new Map();
  const liveUrls = new Set();
  const images = [];
  const requests = [];
  const nodes = {
    preview: { hidden: true, removeAttribute() { delete this.src; } },
    status: {}, metadata: {},
  };
  let timerId = 0;
  let urlId = 0;
  const state = {
    id: '1', timestamp: 'first', stage: null, bodyReads: 0, errorCode: null,
    responses: [],
  };

  function pending(signal) {
    return new Promise((resolve, reject) => {
      state.responses.push(resolve);
      // 响应体读取被取消时，浏览器可能抛 AbortError，而不是自定义 reason。
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    });
  }

  const context = vm.createContext({
    AbortController,
    document: { getElementById: id => nodes[id] },
    window: { addEventListener: (name, callback) => listeners.set(name, callback) },
    URL: {
      createObjectURL() { const url = `blob:${++urlId}`; liveUrls.add(url); return url; },
      revokeObjectURL: url => liveUrls.delete(url),
    },
    Image: class {
      set src(value) {
        this.url = value;
        images.push(this);
        if (state.stage === 'image_error') queueMicrotask(() => this.onerror?.());
        else if (state.stage !== 'image') queueMicrotask(() => this.onload?.());
      }
      removeAttribute() { delete this.url; }
    },
    fetch: async (url, { signal }) => {
      requests.push({ url, signal });
      if (state.stage === 'fetch') await pending(signal);
      const id = state.id;
      const timestamp = state.timestamp;
      return {
        ok: !state.errorCode,
        json: async () => ({ code: state.errorCode }),
        headers: { get: name => name === 'X-Frame-Id' ? id : timestamp },
        blob: async () => {
          state.bodyReads++;
          if (state.stage === 'body') await pending(signal);
          return {};
        },
      };
    },
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
  });

  return {
    state, nodes, requests, images, liveUrls,
    start: () => vm.runInContext(script, context),
    event: (name, persisted = true) => listeners.get(name)?.({ persisted }),
    timerCount: delay => [...timers.values()].filter(timer => timer.delay === delay).length,
    runTimer(delay) {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, `缺少 ${delay} ms 定时器`);
      timers.delete(entry[0]);
      entry[1].callback();
    },
  };
}

test('首次 pageshow 不重复请求；历史缓存恢复后继续更新', async () => {
  const h = harness();
  h.start();
  h.event('pageshow', false);
  await flush();
  assert.equal(h.requests.length, 1);
  assert.equal(h.timerCount(300), 1);
  h.event('pagehide');
  assert.equal(h.liveUrls.size, 0);
  assert.equal(h.nodes.preview.hidden, true);
  h.state.id = '2';
  h.event('pageshow');
  h.event('pageshow');
  await flush();
  assert.equal(h.requests.length, 2);
  assert.equal(h.timerCount(300), 1);
  assert.match(h.nodes.metadata.textContent, /第 2 帧/);
});

for (const stage of ['fetch', 'body', 'image']) {
  test(`${stage} 期间离开并立即恢复，取消旧操作且只有一个轮询链`, async () => {
    const h = harness();
    h.state.stage = stage;
    h.start();
    await flush();
    const oldImage = h.images[0];
    const oldOnload = oldImage?.onload;
    h.event('pagehide');
    h.state.stage = null;
    h.state.id = '2';
    h.event('pageshow');
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].signal.aborted, true);
    await flush();
    assert.equal(h.timerCount(300), 1);
    assert.equal(h.liveUrls.size, 0);
    assert.equal(h.nodes.preview.hidden, true);
    h.runTimer(300);
    await flush();
    oldOnload?.();
    await flush();
    assert.equal(h.requests.length, 2);
    assert.equal(h.timerCount(300), 1);
    assert.equal(h.liveUrls.size, 1);
    assert.match(h.nodes.metadata.textContent, /第 2 帧/);
  });

  test(`${stage} 停滞时五秒超时，清除旧图并自动重试`, async () => {
    const h = harness();
    h.start();
    await flush();
    h.state.id = '2';
    h.state.stage = stage;
    h.runTimer(300);
    await flush();
    h.runTimer(5000);
    await flush();
    assert.equal(h.requests[1].signal.aborted, true);
    assert.equal(h.nodes.preview.hidden, true);
    assert.match(h.nodes.status.textContent, /超时/);
    assert.equal(h.liveUrls.size, 0);
    assert.equal(h.timerCount(300), 1);
    h.state.stage = null;
    h.runTimer(300);
    await flush();
    assert.equal(h.nodes.preview.hidden, false);
    assert.match(h.nodes.metadata.textContent, /第 2 帧/);
  });
}

test('重复帧消费响应体但不重新加载图片；重启后重复或较小的帧号仍更新', async () => {
  const h = harness();
  h.state.id = '8';
  h.start();
  await flush();
  const originalUrl = h.nodes.preview.src;
  h.runTimer(300);
  await flush();
  assert.equal(h.state.bodyReads, 2);
  assert.equal(h.images.length, 1);
  assert.equal(h.nodes.preview.src, originalUrl);
  h.state.timestamp = 'restarted';
  h.runTimer(300);
  await flush();
  assert.equal(h.images.length, 2);
  h.state.id = '1';
  h.runTimer(300);
  await flush();
  assert.equal(h.images.length, 3);
  assert.match(h.nodes.metadata.textContent, /第 1 帧/);
  assert.equal(h.liveUrls.size, 1);
});

test('缺少帧标识时持续显示新响应', async () => {
  const h = harness();
  h.state.id = null;
  h.start();
  await flush();
  h.runTimer(300);
  await flush();
  assert.equal(h.images.length, 2);
});

test('离开后不恢复时，取消的请求不产生新轮询或残留 Blob', async () => {
  const h = harness();
  h.state.stage = 'image';
  h.start();
  await flush();
  h.event('pagehide');
  await flush();
  assert.equal(h.timerCount(300), 0);
  assert.equal(h.timerCount(5000), 0);
  assert.equal(h.liveUrls.size, 0);
});

for (const failure of ['capture_failed', 'image_error']) {
  test(`${failure} 隐藏旧图，恢复后相同帧也重新加载`, async () => {
    const h = harness();
    h.start();
    await flush();
    h.state.timestamp = 'next';
    if (failure === 'image_error') h.state.stage = failure;
    else h.state.errorCode = failure;
    h.runTimer(300);
    await flush();
    assert.equal(h.nodes.preview.hidden, true);
    assert.equal(h.liveUrls.size, 0);
    assert.match(h.nodes.status.textContent, failure === 'image_error' ? /图片加载失败/ : /采集任务异常停止/);
    h.state.stage = null;
    h.state.errorCode = null;
    h.state.timestamp = 'first';
    h.runTimer(300);
    await flush();
    assert.equal(h.nodes.preview.hidden, false);
    assert.equal(h.liveUrls.size, 1);
    assert.equal(h.timerCount(300), 1);
  });
}
