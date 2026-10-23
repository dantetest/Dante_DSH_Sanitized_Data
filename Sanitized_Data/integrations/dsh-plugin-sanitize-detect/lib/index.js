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
import { homedir } from 'node:os';
import { join } from 'node:path';

export const name = 'blctek-sanitize-detect-host';
export const inject = ['webServer'];

const PREFIX = '/sanitized-data/api';

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

export function apply(ctx) {
  return ctx.effect(
    () => ctx.webServer.register({
      kind: 'prefix',
      path: PREFIX,
      handler: proxyHandler,
    }),
    'sanitize-detect: loopback workbench proxy',
  );
}
