// 审核诊断：执行真实预览脚本，以最小 DOM 模型检查恢复事件后的调度。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const root = path.resolve(__dirname, '../../../..');
const html = fs.readFileSync(path.join(root, 'src/browscreen/preview.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('__INTERVAL_MS__', '300');
const listeners = new Map();
const timers = new Map();
let requests = 0;
let timerId = 0;
const nodes = {
  preview: {
    set src(value) { queueMicrotask(() => this.onload?.()); },
    removeAttribute() {},
  },
  status: {},
  metadata: {},
};
const context = vm.createContext({
  document: { getElementById: id => nodes[id] },
  window: { addEventListener: (name, callback) => listeners.set(name, callback) },
  URL: { createObjectURL: () => 'blob:review', revokeObjectURL() {} },
  fetch: async () => {
    requests++;
    return { ok: true, blob: async () => ({}), headers: { get: () => '1' } };
  },
  setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
  clearTimeout: id => timers.delete(id),
});

(async () => {
  vm.runInContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests, 1);
  assert.equal(timers.size, 1);
  listeners.get('pagehide')({ persisted: true });
  listeners.get('pageshow')?.({ persisted: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(timers.size, 0);
  assert.equal(vm.runInContext('stopped', context), true);
  assert.equal(requests, 1);
  console.log(`R-03: stopped=true, scheduled_polls=${timers.size}, requests=${requests}, status=${nodes.status.textContent}`);
  console.log('这是脚本级生命周期复现，未验证真实浏览器是否将该页面放入 bfcache。');
})().catch(error => { console.error(error); process.exitCode = 1; });
