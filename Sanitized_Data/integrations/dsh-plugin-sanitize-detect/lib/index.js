/**
 * 数据隐站 · DSH MCP L2 检测插件（host 侧）
 *
 * 浏览器（DSH Web/桌面）不能直接跨源访问数据隐站 8760，这里在本机 DSH 的
 * WebServer 上注册一个只读/写本机回环代理：
 *   DSH 页面 → /sanitized-data/api/... → http://127.0.0.1:<runtime端口>/api/...
 *
 * 只解析本机 runtime.json / YINZHAN_HOME，不访问任何外网；不落盘、不改数据。
 */
import { readFileSync } from 'node:fs';
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';

export const name = 'blctek-sanitize-detect-host';
export const inject = ['webServer'];

const PREFIX = '/sanitized-data/api';
const LOCAL = /^http:\/\/127\.0\.0\.1:\d+$/;

function candidateHomes() {
  return [...new Set([
    process.env.YINZHAN_HOME,
    'D:\\DSH_DESKTOP_WORKSPACE\\Sanitized_Data',
    join(homedir(), 'AppData', 'Local', 'Programs', 'Sanitized_Data'),
  ].filter(Boolean))];
}

async function healthy(base) {
  try {
    const response = await fetch(base + '/api/health', { signal: AbortSignal.timeout(1500) });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitHealthy(base, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await healthy(base)) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function ensureWorkbench() {
  const base = resolveBase();
  if (await healthy(base)) return { ok: true, started: false, base };
  const candidates = [];
  for (const home of candidateHomes()) {
    const exe = join(home, 'Sanitized_Data.exe');
    if (existsSync(exe)) candidates.push({ kind: 'exe', file: exe, cwd: home });
    const script = join(home, 'app', 'server', 'run.py');
    if (existsSync(script)) candidates.push({ kind: 'python', file: 'pythonw', args: ['-X', 'utf8', script, '--no-browser'], cwd: home });
  }
  for (const candidate of candidates) {
    try {
      const child = spawn(candidate.file, candidate.args || [], {
        cwd: candidate.cwd, detached: true, stdio: 'ignore', windowsHide: true,
      });
      child.unref();
      if (await waitHealthy(base)) {
        return { ok: true, started: true, base, via: candidate.kind };
      }
    } catch {
      // 继续尝试下一个候选；最终返回明确失败。
    }
  }
  return { ok: false, started: false, base, error: 'workbench_start_failed' };
}


function candidateRuntimeFiles() {
  const out = [];
  const add = (value) => {
    if (!value) return;
    const text = String(value);
    out.push(join(text, 'runtime.json'));
    out.push(join(text, 'data', 'runtime.json'));
  };
  add(process.env.YINZHAN_HOME);
  add(process.env.YINZHAN_DATA_DIR);
  add(join(homedir(), 'AppData', 'Local', 'Sanitized_Data', 'data'));
  add(join(process.cwd(), 'data'));
  add(join(process.cwd(), 'Sanitized_Data', 'data'));
  add('D:\\DSH_DESKTOP_WORKSPACE\\Sanitized_Data\\data');
  return [...new Set(out)];
}

function resolveBase() {
  const explicit = process.env.YINZHAN_URL;
  if (explicit && /^http:\/\/127\.0\.0\.1:\d+$/.test(explicit)) return explicit.replace(/\/$/, '');
  for (const file of candidateRuntimeFiles()) {
    try {
      const raw = JSON.parse(readFileSync(file, 'utf8'));
      const port = Number(raw.port);
      if (Number.isInteger(port) && port > 0) return `http://127.0.0.1:${port}`;
    } catch {
      // 没有运行中的工作台是正常情况；继续找下一个候选。
    }
  }
  return 'http://127.0.0.1:8760';
}

async function readRequestBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return chunks.length ? Buffer.concat(chunks) : undefined;
}

function proxyHandler(req, res) {
  (async () => {
    if (req.method === 'POST' && req.url === PREFIX + '/start') {
      const result = await ensureWorkbench();
      res.writeHead(result.ok ? 200 : 503, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(result));
      return;
    }
    if (req.method === 'GET' && req.url === PREFIX) {
      res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: true, proxy: PREFIX, target: resolveBase() }));
      return;
    }
    let incoming;
    try {
      incoming = new URL(req.url, 'http://dsh.local');
    } catch {
      res.writeHead(400, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: { code: 'bad_request' } }));
      return;
    }
    if (!incoming.pathname.startsWith(PREFIX + '/')) {
      res.writeHead(404, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: { code: 'not_found' } }));
      return;
    }
    const suffix = incoming.pathname.slice(PREFIX.length);
    const target = resolveBase() + '/api' + suffix + incoming.search;
    const headers = { accept: req.headers.accept || 'application/json' };
    if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];
    let body;
    if (!['GET', 'HEAD'].includes((req.method || 'GET').toUpperCase())) {
      body = await readRequestBody(req);
      if (body !== undefined) headers['content-length'] = String(body.length);
    }
    try {
      const upstream = await fetch(target, {
        method: req.method || 'GET',
        headers,
        body,
        redirect: 'manual',
      });
      const buffer = Buffer.from(await upstream.arrayBuffer());
      const responseHeaders = {
        'content-type': upstream.headers.get('content-type') || 'application/json; charset=utf-8',
        'cache-control': 'no-store',
      };
      res.writeHead(upstream.status, responseHeaders);
      res.end(buffer);
    } catch (error) {
      res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({
        error: {
          code: 'sanitize_workbench_unavailable',
          message: '数据隐站工作台未运行或不可达：请先启动数据隐站（start.bat / 桌面图标）。',
          detail: String(error && error.message ? error.message : error).slice(0, 300),
          target,
        },
      }));
    }
  })().catch((error) => {
    try {
      res.writeHead(500, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: { code: 'proxy_failed', message: String(error?.message || error) } }));
    } catch {
      // 连接已断，忽略。
    }
  });
}

let warmupPromise = null;

function warmupWorkbench() {
  if (process.env.YINZHAN_NO_WARMUP === '1') return null;
  if (!warmupPromise) {
    // DSH 启动后在后台拉起/确认工作台，避免重启后第一次点左下角时
    // 浏览器先打开一个 127.0.0.1:8760，服务却还没开始监听（ERR_CONNECTION_REFUSED）。
    warmupPromise = ensureWorkbench().catch(() => null);
  }
  return warmupPromise;
}

export function apply(ctx) {
  void warmupWorkbench();
  return ctx.effect(
    () => ctx.webServer.register({
      kind: 'prefix',
      path: PREFIX,
      handler: proxyHandler,
    }),
    'sanitize-detect: loopback workbench proxy',
  );
}
