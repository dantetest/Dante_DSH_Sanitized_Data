/**
 * 数据隐站 · DSH MCP L2 检测插件（client 侧）
 *
 * - 工具卡：接管 sanitize_document / sanitize_file / export_sanitize_job 等工具调用，
 *   显示「已隐去 N 处 / 自检 / job / 待确认」以及“在检测面板复核 / 打开工作台”。
 * - 右侧面板：概览、实体、审计、预览；操作走 host 代理（同源 /sanitized-data/api）。
 * - 侧边栏底部入口：随时打开「脱敏检测」。
 *
 * 本文件是 DSH client bundle 的 factory 形式（window.__ModuleLoader__.load），
 * 不使用 JSX/构建工具，只依赖平台提供的 react。
 */
window.__ModuleLoader__.load({
  id: '@blctek/dsh-plugin-sanitize-detect',
  factory: (require) => {
    const React = require('react');
    const h = React.createElement;
    const PREFIX = '/sanitized-data/api';
    const PANEL_ID = '@blctek/dsh-plugin-sanitize-detect';
    const PANEL_KIND = 'sanitize-detect';
    const TOOL_KEYS = [
      'sanitize_document', 'redact_text', 'sanitize_file', 'get_sanitize_job',
      'list_sanitize_jobs', 'get_sanitize_entities', 'update_sanitize_entity',
      'mark_sanitize_region', 'accept_all_sanitize_entities', 'export_sanitize_job',
      'get_sanitize_audit', 'rehydrate_sanitize_text', 'open_sanitize_workbench',
    ];

    // 工具卡与面板共享“当前 job”——同一会话内点击工具卡后，右侧面板直接打开它。
    const uiState = { jobId: null, listeners: new Set() };
    function setCurrentJob(jobId) {
      const value = jobId ? String(jobId) : null;
      if (uiState.jobId === value) return;
      uiState.jobId = value;
      for (const fn of [...uiState.listeners]) {
        try { fn(value); } catch { /* 单个订阅者失败不影响其他 */ }
      }
    }
    function subscribeJob(fn) {
      uiState.listeners.add(fn);
      return () => uiState.listeners.delete(fn);
    }

    const COLORS = {
      ink: 'var(--dsw-alias-label-primary, #1f2328)',
      secondary: 'var(--dsw-alias-label-secondary, #59636e)',
      tertiary: 'var(--dsw-alias-label-tertiary, #818b98)',
      bg: 'var(--dsw-alias-bg-base, #ffffff)',
      subtle: 'var(--dsw-alias-bg-subtle, #f6f8fa)',
      border: 'var(--dsw-alias-border-l1, #d1d9e0)',
      green: 'var(--dsw-alias-state-success-primary, #1a7f37)',
      greenBg: 'var(--dsw-alias-state-success-subtle, #dafbe1)',
      amber: 'var(--dsw-alias-state-warn-primary, #9a6700)',
      amberBg: 'var(--dsw-alias-state-warn-subtle, #fff8c5)',
      red: 'var(--dsw-alias-state-error-primary, #cf222e)',
      redBg: 'var(--dsw-alias-state-error-subtle, #ffebe9)',
      blue: 'var(--dsw-alias-state-info-primary, #0969da)',
      blueBg: 'var(--dsw-alias-state-info-subtle, #ddf4ff)',
    };

    function shortId(id) {
      const text = String(id || '');
      return text.length > 12 ? text.slice(0, 4) + '…' + text.slice(-4) : text;
    }

    function parsePrint(value) {
      if (Array.isArray(value)) return value.map(parsePrint).filter(Boolean).join(' · ');
      if (value && typeof value === 'object') {
        try { return JSON.stringify(value); } catch { return String(value); }
      }
      return value === null || value === undefined ? '' : String(value);
    }

    function resultText(block) {
      if (!block || !Array.isArray(block.content)) return '';
      const parts = [];
      for (const item of block.content) {
        if (item && item.type === 'text' && typeof item.text === 'string') parts.push(item.text);
      }
      return parts.join('\n');
    }

    function toolPayload(block) {
      const text = resultText(block);
      if (!text) return null;
      const trimmed = text.trim();
      if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return null;
      try { return JSON.parse(trimmed); } catch { return null; }
    }

    function argsOf(block) {
      const raw = block && (block.argsRaw || (block.call && block.call.argsRaw));
      if (!raw) return {};
      try { return JSON.parse(raw); } catch { return {}; }
    }

    function pill(text, tone) {
      const map = {
        ok: [COLORS.green, COLORS.greenBg], warn: [COLORS.amber, COLORS.amberBg],
        error: [COLORS.red, COLORS.redBg], info: [COLORS.blue, COLORS.blueBg],
      };
      const [fg, bg] = map[tone] || [COLORS.secondary, COLORS.subtle];
      return h('span', {
        style: {
          color: fg, background: bg, borderRadius: 999, padding: '1px 8px',
          fontSize: 11, lineHeight: '18px', whiteSpace: 'nowrap',
        },
      }, text);
    }

    function button(label, onClick, { primary = false, disabled = false } = {}) {
      return h('button', {
        type: 'button', disabled, onClick: (event) => {
          if (event && event.stopPropagation) event.stopPropagation();
          if (!disabled) onClick();
        },
        style: {
          appearance: 'none', border: primary ? 'none' : `1px solid ${COLORS.border}`,
          background: primary ? 'var(--dsw-alias-button-primary, #1f6feb)' : COLORS.bg,
          color: primary ? '#fff' : COLORS.ink,
          borderRadius: 7, padding: '4px 10px', fontSize: 12, cursor: disabled ? 'default' : 'pointer',
          opacity: disabled ? 0.5 : 1, whiteSpace: 'nowrap',
        },
      }, label);
    }

   function toolSummary(toolName, payload, rawText, phase) {
      if (phase !== 'result') return phase === 'preparing' ? '准备调用…' : '调用中…';
      if (!payload) {
        const first = (rawText || '').trim().split('\n')[0] || '';
        return first.length > 90 ? first.slice(0, 90) + '…' : first || '完成';
      }
      if (payload.ok === false) return payload.error?.message || payload.code || '失败';
      if (toolName === 'redact_text') {
        return `已替换 ${payload.entities || 0} 处` + (payload.by_type ? ' · ' + Object.keys(payload.by_type).join('/') : '');
      }
      const counts = payload.counts || {};
      const total = counts.total ?? payload.entities ?? 0;
      const pending = counts.pending ?? 0;
      const residual = payload.verify && payload.verify.residual;
      const verified = payload.verify && payload.verify.verified;
      const bits = [`已隐去 ${total} 处`];
      if (pending) bits.push(`待确认 ${pending}`);
      if (verified === true) bits.push('自检通过');
      else if (residual !== undefined && residual !== null) bits.push(`残留 ${residual}`);
      if (payload.job_id) bits.push('job ' + shortId(payload.job_id));
      return bits.join(' · ');
    }

    function ToolCard(props) {
      const toolName = props.toolName || 'sanitize';
      const baseName = String(toolName).split('__').pop();
      const phase = props.phase || (props.block && props.block.phase) || 'start';
      const block = props.block || {};
      const payload = phase === 'result' ? toolPayload(block) : null;
      const raw = phase === 'result' ? resultText(block) : (block.argsRaw || '');
      const args = argsOf(block);
      const [open, setOpen] = React.useState(false);
      const [busy, setBusy] = React.useState(false);
      const failed = phase === 'result' && ((block && block.isError) || (payload && payload.ok === false));
      const tone = failed ? 'error' : phase === 'result' ? 'ok' : 'info';
      const jobId = payload && payload.job_id;
      const workbench = payload && payload.workbench_url;

      function openPanel() {
        if (!jobId) return;
        setCurrentJob(jobId);
        const url = workbench || `http://127.0.0.1:8760/console?job=${encodeURIComponent(jobId)}`;
        // 优先使用 DSH 原生右侧浏览器 tab，直接显示真实工作台（不是仿制面板）。
        if (openWorkbenchTab(url)) return;
        try {
          const sr = sidebarRight();
          if (sr && typeof sr.openTab === 'function') sr.openTab(PANEL_KIND);
          else window.open(url, '_blank', 'noopener');
        } catch {
          window.open(url, '_blank', 'noopener');
        }
      }

      function exportHuman() {
        if (!jobId || busy) return;
        setBusy(true);
        fetch(`${PREFIX}/jobs/${encodeURIComponent(jobId)}/export`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ mode: 'human' }),
        }).then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data?.error?.message || '导出失败');
        }).catch((error) => {
          window.alert('涂黑版导出失败：' + (error?.message || error));
        }).finally(() => setBusy(false));
      }

      const titleMap = {
        sanitize_document: '文件脱敏', sanitize_file: '建立脱敏任务',
        redact_text: '文本脱敏', get_sanitize_job: '查询脱敏任务',
        list_sanitize_jobs: '脱敏任务列表', get_sanitize_entities: '实体清单',
        update_sanitize_entity: '更新实体', mark_sanitize_region: '框选补漏',
        accept_all_sanitize_entities: '全部接受', export_sanitize_job: '导出成品',
        get_sanitize_audit: '审计链', rehydrate_sanitize_text: '本机还原',
        open_sanitize_workbench: '工作台深链', check_support: '格式检查',
        list_rules: '规则包',
      };
      const title = titleMap[baseName] || baseName;
      const summary = toolSummary(toolName, payload, raw, phase);
      const detailText = raw.length > 4000 ? raw.slice(0, 4000) + '\n…' : raw;

      return h('div', {
        'data-tool': toolName, 'data-state': failed ? 'error' : phase === 'result' ? 'ok' : 'running',
        style: {
          border: `1px solid ${COLORS.border}`, borderRadius: 10, background: COLORS.bg,
          margin: '4px 0', overflow: 'hidden', fontFamily: 'var(--dsh-font-ui, system-ui)',
        },
      },
      h('div', {
        role: 'button', tabIndex: 0,
        onClick: () => setOpen((value) => !value),
        onKeyDown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setOpen((v) => !v); }
        },
        style: {
          display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
          cursor: 'pointer', userSelect: 'none',
        },
      },
      h('span', {
        style: {
          width: 24, height: 24, borderRadius: 7, flex: 'none',
          background: failed ? COLORS.redBg : COLORS.blueBg,
          color: failed ? COLORS.red : COLORS.blue,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 12, fontWeight: 600,
        },
      }, '隐'),
      h('span', { style: { fontWeight: 600, fontSize: 13, color: COLORS.ink, flex: 'none' } }, '数据隐站'),
      h('span', { style: { color: COLORS.tertiary, fontSize: 12, flex: 'none' } }, '·'),
      h('span', { style: { fontSize: 13, color: COLORS.ink, flex: 'none' } }, title),
      pill(failed ? '失败' : phase === 'result' ? '已脱敏' : '运行中', tone),
      h('span', {
        style: {
          color: failed ? COLORS.red : COLORS.secondary, fontSize: 12,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1,
        },
      }, summary),
      h('span', { style: { color: COLORS.tertiary, fontSize: 11 } }, open ? '收起' : '详情'),
      ),
      open && h('div', { style: { borderTop: `1px solid ${COLORS.border}`, padding: '8px 10px' } },
        payload && h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 8 } },
          payload.output && h('div', null,
            h('div', { style: { fontSize: 10, color: COLORS.tertiary } }, '可外发文件'),
            h('code', { style: { fontSize: 11, wordBreak: 'break-all' } }, payload.output)),
          payload.job_id && h('div', null,
            h('div', { style: { fontSize: 10, color: COLORS.tertiary } }, 'Job'),
            h('code', { style: { fontSize: 11 } }, payload.job_id)),
          payload.verify && h('div', null,
            h('div', { style: { fontSize: 10, color: COLORS.tertiary } }, '出口自检'),
            h('span', { style: { fontSize: 11, color: payload.verify.verified ? COLORS.green : COLORS.red } },
              payload.verify.verified ? `通过 · 残留 ${payload.verify.residual ?? 0}` : '未通过')),
          payload.mode && h('div', null,
            h('div', { style: { fontSize: 10, color: COLORS.tertiary } }, '模式'),
            h('span', { style: { fontSize: 11 } }, payload.mode === 'human' ? '涂黑版' : '换名版（可还原）')),
        ),
        h('pre', {
          style: {
            margin: 0, padding: 8, background: COLORS.subtle, borderRadius: 6,
            fontSize: 11, lineHeight: 1.5, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap',
            wordBreak: 'break-all', color: COLORS.secondary,
          },
        }, detailText || '(无详情)'),
        h('div', { style: { display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' } },
          jobId && button('打开工作台', openPanel, { primary: true }),
          jobId && button(busy ? '导出中…' : '导出涂黑版', exportHuman, { disabled: busy }),
        ),
      ),
      );
    }

    async function apiJson(path, options = {}) {
      const response = await fetch(PREFIX + path, {
        ...options,
        headers: { accept: 'application/json', ...(options.headers || {}) },
      });
      const text = await response.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = null; }
      if (!response.ok) {
        const message = data?.error?.message || data?.error?.code || `HTTP ${response.status}`;
        throw new Error(message);
      }
      return data;
    }

    function Metric({ label, value, tone }) {
      return h('div', {
        style: {
          flex: 1, minWidth: 70, padding: '8px 9px', borderRadius: 8,
          background: COLORS.subtle, border: `1px solid ${COLORS.border}`,
        },
        title: label,
      },
      h('div', { style: { fontSize: 17, fontWeight: 650, color: tone || COLORS.ink, lineHeight: '22px' } }, value),
      h('div', { style: { fontSize: 10, color: COLORS.tertiary, marginTop: 2 } }, label));
    }

    function DetectPanel() {
      const [jobId, setJobId] = React.useState(uiState.jobId);
      const [job, setJob] = React.useState(null);
      const [entities, setEntities] = React.useState([]);
      const [audit, setAudit] = React.useState(null);
      const [tab, setTab] = React.useState('overview');
      const [error, setError] = React.useState('');
      const [busy, setBusy] = React.useState(false);
      const [notice, setNotice] = React.useState('');

      React.useEffect(() => subscribeJob(setJobId), []);

      const load = React.useCallback(async (targetId) => {
        setError('');
        try {
          let id = targetId;
          if (!id) {
            const list = await apiJson('/jobs');
            const jobs = Array.isArray(list?.jobs) ? list.jobs : [];
            const pick = jobs.find((item) => item.origin === 'mcp') || jobs[0] || null;
            id = pick && pick.id ? pick.id : null;
          }
          if (!id) {
            setJob(null); setEntities([]); setAudit(null); setJobId(null);
            return;
          }
          const [snapshot, ents, auditData] = await Promise.all([
            apiJson('/jobs/' + encodeURIComponent(id)),
            apiJson('/jobs/' + encodeURIComponent(id) + '/entities'),
            apiJson('/audit?limit=20').catch(() => null),
          ]);
          setJobId(id);
          setJob(snapshot || null);
          setEntities(Array.isArray(ents?.entities) ? ents.entities : []);
          setAudit(auditData);
        } catch (loadError) {
          setError(loadError?.message || String(loadError));
        }
      }, []);

      React.useEffect(() => { load(jobId); }, [jobId, load]);

      const refresh = () => load(jobId);
      const counts = job?.counts || {};
      const pending = counts.pending ?? entities.filter((e) => e.status === 'pending').length;
      const total = counts.total ?? entities.length;
      const byType = counts.by_type || {};
      const auditOk = audit && Array.isArray(audit.records)
        ? audit.records.every((record) => Number.isInteger(record.bad) ? record.bad === 0 : true) : null;
      const workbench = jobId ? `http://127.0.0.1:${job?.runtime_port || 8760}/console?job=${encodeURIComponent(jobId)}` : null;

      async function post(path, body) {
        if (busy) return;
        setBusy(true); setNotice('');
        try {
          await apiJson(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) });
          await refresh();
          setNotice('已完成');
        } catch (actionError) {
          setNotice(actionError?.message || String(actionError));
        } finally {
          setBusy(false);
        }
      }

      const tabButton = (id, label) => h('button', {
        type: 'button', onClick: () => setTab(id),
        style: {
          appearance: 'none', border: 'none', background: tab === id ? COLORS.blueBg : 'transparent',
          color: tab === id ? COLORS.blue : COLORS.secondary, borderRadius: 6,
          padding: '4px 9px', fontSize: 12, cursor: 'pointer',
        },
      }, label);

      return h('div', {
        style: {
          display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0,
          background: COLORS.bg, color: COLORS.ink,
          fontFamily: 'var(--dsh-font-ui, system-ui, sans-serif)',
        },
      },
      h('div', { style: { padding: '10px 12px 8px', borderBottom: `1px solid ${COLORS.border}` } },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
          h('span', { style: { fontSize: 13, fontWeight: 650 } }, '脱敏检测'),
          job?.origin === 'mcp' && pill('MCP', 'info'),
          job?.stage && pill(job.stage === 'ready' ? '已就绪' : job.stage, job.stage === 'error' ? 'error' : 'ok'),
          h('span', { style: { flex: 1 } }),
          button(error ? '重试' : '刷新', refresh),
        ),
        h('div', { style: { fontSize: 12, color: COLORS.secondary, marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
          job ? (job.name || job.id) : '尚未选择任务'),
        error && h('div', { style: { marginTop: 7, color: COLORS.red, fontSize: 12, lineHeight: 1.5 } },
          error,
          h('div', { style: { marginTop: 3, color: COLORS.secondary } },
            '启动数据隐站工作台（桌面“数据隐站工作台”快捷方式或 启动.bat）后，点右上角“重试”。')),
      ),
      h('div', { style: { display: 'flex', gap: 4, padding: '6px 10px', borderBottom: `1px solid ${COLORS.border}` } },
        tabButton('overview', '概览'), tabButton('entities', '实体'), tabButton('audit', '审计')),
      h('div', { style: { flex: 1, minHeight: 0, overflow: 'auto', padding: 10 } },
        !job && !error && h('div', { style: { color: COLORS.tertiary, fontSize: 12, lineHeight: 1.8 } },
          '还没有 MCP 脱敏任务。',
          h('br'),
          '在 DSH 对话里让 Agent 调用 sanitize_file / sanitize_document，或先用工作台建任务。'),
        job && tab === 'overview' && h('div', null,
          h('div', { style: { display: 'flex', gap: 6 } },
            h(Metric, { label: '已隐去', value: total, tone: COLORS.ink }),
            h(Metric, { label: '待确认', value: pending, tone: pending ? COLORS.amber : COLORS.green }),
            h(Metric, { label: '自检', value: job.exports?.ai?.verify?.verified === false ? '失败' : '待导出', tone: COLORS.green })),
          h('div', { style: { marginTop: 10, fontSize: 12, lineHeight: 1.8, color: COLORS.secondary } },
            h('div', null, '类型分布：', Object.keys(byType).length
              ? Object.entries(byType).map(([key, value]) => key + ' ×' + value).join('、') : '—'),
            job.warnings && job.warnings.length
              ? h('div', { style: { marginTop: 5, color: COLORS.amber } }, '提示：' + job.warnings.slice(0, 2).join(' / ')) : null,
            h('div', { style: { marginTop: 5 } }, '来源：', job.origin === 'mcp' ? 'MCP / Agent 调用' : '工作台'),
          ),
          jobId && h('div', { style: { marginTop: 10, borderRadius: 8, overflow: 'hidden', border: `1px solid ${COLORS.border}` } },
            h('img', {
              src: `${PREFIX}/jobs/${encodeURIComponent(jobId)}/render/0?mode=out`,
              alt: '可外发版预览', style: { display: 'block', width: '100%', height: 'auto' },
            })),
          h('div', { style: { display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' } },
            jobId && button('全部接受', () => post(`/jobs/${encodeURIComponent(jobId)}/accept-all`, {}), { disabled: busy }),
            jobId && button('导出换名版', () => post(`/jobs/${encodeURIComponent(jobId)}/export`, { mode: 'ai' }), { primary: true, disabled: busy }),
            jobId && button('导出涂黑版', () => post(`/jobs/${encodeURIComponent(jobId)}/export`, { mode: 'human' }), { disabled: busy }),
          ),
          notice && h('div', { style: { marginTop: 8, fontSize: 12, color: notice === '已完成' ? COLORS.green : COLORS.red } }, notice),
          h('div', { style: { marginTop: 10, fontSize: 11, color: COLORS.tertiary, lineHeight: 1.6 } },
            '预览/操作由本机数据隐站工作台提供；出口自检不通过时成品会被删除。'),
        ),
        job && tab === 'entities' && h('div', null,
          entities.length === 0 && h('div', { style: { color: COLORS.tertiary, fontSize: 12 } }, '没有实体。'),
          entities.map((entity) => h('div', {
            key: entity.id,
            style: {
              border: `1px solid ${COLORS.border}`, borderRadius: 8, padding: '7px 9px',
              marginBottom: 6, background: entity.status === 'pending' ? COLORS.amberBg : COLORS.bg,
            },
          },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 6 } },
            h('span', { style: { fontSize: 12, fontWeight: 600 } }, entity.label || entity.etype || '实体'),
            pill(entity.status || 'accepted', entity.status === 'pending' ? 'warn' : 'ok'),
            h('span', { style: { flex: 1 } }),
            h('code', { style: { fontSize: 10, color: COLORS.tertiary } }, shortId(entity.id))),
          h('div', { style: { marginTop: 3, fontSize: 12, color: COLORS.secondary, wordBreak: 'break-all' } },
            entity.value !== undefined ? `${entity.value}` : '(值不返回给模型)'),
          h('div', { style: { display: 'flex', gap: 6, marginTop: 5, flexWrap: 'wrap' } },
            jobId && button('接受', () => post(`/jobs/${encodeURIComponent(jobId)}/entities/${encodeURIComponent(entity.id)}`, { action: 'accept' }), { disabled: busy }),
            jobId && button('拒绝', () => post(`/jobs/${encodeURIComponent(jobId)}/entities/${encodeURIComponent(entity.id)}`, { action: 'reject' }), { disabled: busy }),
            jobId && button('替换', () => {
              const replacement = window.prompt('替换成（假名/假号）：');
              if (replacement) post(`/jobs/${encodeURIComponent(jobId)}/entities/${encodeURIComponent(entity.id)}`, { action: 'replace', replacement });
            }, { disabled: busy })),
          )),
        ),
        job && tab === 'audit' && h('div', null,
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 } },
            h('span', { style: { fontSize: 12, fontWeight: 600 } }, '审计链'),
            audit?.stats && h('span', { style: { fontSize: 11, color: COLORS.tertiary } }, `共 ${audit.stats.records ?? '—'} 条`),
            h('span', { style: { flex: 1 } }),
            h('span', { style: { fontSize: 11, color: auditOk === false ? COLORS.red : COLORS.green } },
              auditOk === false ? '校验失败' : auditOk === true ? 'HMAC 链校验通过' : '仅本机')),
          (audit?.records || []).slice(0, 20).map((record) => h('div', {
            key: record.seq || record.id,
            style: { borderBottom: `1px solid ${COLORS.border}`, padding: '6px 0', fontSize: 11, color: COLORS.secondary },
          },
          h('div', { style: { display: 'flex', gap: 6, alignItems: 'center' } },
            h('span', { style: { color: COLORS.tertiary } }, '#' + (record.seq ?? '?')),
            h('span', { style: { color: COLORS.ink } }, record.out_name || record.doc_name || '记录'),
            h('span', { style: { flex: 1 } }),
            h('span', { style: { color: COLORS.tertiary } }, String(record.ts || '').slice(0, 19).replace('T', ' '))),
          h('div', { style: { marginTop: 2, color: COLORS.tertiary, wordBreak: 'break-all' } },
            (record.mode === 'human' ? '涂黑' : '换名') + ' · ' +
            (record.counts?.total ?? '—') + ' 实体 · seal ' + (record.seal_no || record.out_name || '—')),
          )),
          !audit?.records?.length && h('div', { style: { color: COLORS.tertiary, fontSize: 12 } }, '还没有审计记录（导出后生成）。'),
        ),
      ),
      jobId && h('div', { style: { borderTop: `1px solid ${COLORS.border}`, padding: '7px 10px', display: 'flex', gap: 6, flexWrap: 'wrap' } },
        h('a', {
          href: `http://127.0.0.1:${job?.runtime_port || 8760}/console?job=${encodeURIComponent(jobId)}`,
          target: '_blank', rel: 'noopener',
          style: { fontSize: 12, color: COLORS.blue, textDecoration: 'none', alignSelf: 'center' },
        }, '打开完整工作台 ↗'),
        h('span', { style: { flex: 1 } }),
        h('span', { style: { fontSize: 10, color: COLORS.tertiary, alignSelf: 'center' } }, '本机处理 · 不联网'),
      ),
      );
    }

    function sidebarRight() {
      return (typeof ctxRef.get === 'function' && ctxRef.get('sidebarRight')) || ctxRef.sidebarRight;
    }

    function openWorkbenchTab(url) {
      if (!url) return false;
      try {
        const sr = sidebarRight();
        if (!sr || typeof sr.openTab !== 'function') return false;
        const tabs = typeof ctxRef.get === 'function' && ctxRef.get('sidebarRightTabs');
        if (tabs && typeof tabs.get === 'function' && !tabs.get('browser')) return false;
        sr.openTab('browser', { params: { url } });
        return true;
      } catch {
        return false;
      }
    }

    async function latestWorkbenchUrl() {
      let base = 'http://127.0.0.1:8760';
      try {
        const info = await fetch(PREFIX, { headers: { accept: 'application/json' } }).then((r) => r.json());
        if (info && typeof info.target === 'string') base = info.target;
        const list = await fetch(PREFIX + '/jobs', { headers: { accept: 'application/json' } }).then((r) => r.json());
        const jobs = Array.isArray(list && list.jobs) ? list.jobs : [];
        const pick = jobs.find((item) => item && item.origin === 'mcp') || jobs[0] || null;
        return base + '/console' + (pick && pick.id ? '?job=' + encodeURIComponent(pick.id) : '');
      } catch {
        return base + '/console';
      }
    }

    function PanelEntryButton() {
      const open = async () => {
        const url = await latestWorkbenchUrl();
        if (openWorkbenchTab(url)) return;
        try {
          const sr = sidebarRight();
          if (sr && typeof sr.openTab === 'function') sr.openTab(PANEL_KIND);
          else window.open(url, '_blank', 'noopener');
        } catch {
          window.open(url, '_blank', 'noopener');
        }
      };
      return h('button', {
        type: 'button', onClick: open, title: '数据隐站工作台',
        style: {
          appearance: 'none', border: 'none', background: 'transparent', color: 'inherit',
          cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '4px 6px', borderRadius: 6, fontSize: 12,
        },
      }, h('span', {
        style: { width: 18, height: 18, borderRadius: 5, background: COLORS.blueBg, color: COLORS.blue,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700 },
      }, '隐'), '数据隐站工作台');
    }

    const ctxRef = { get: null, sidebarRight: null };

    function apply(ctx) {
      ctxRef.get = typeof ctx.get === 'function' ? ctx.get.bind(ctx) : null;
      ctxRef.sidebarRight = ctx.sidebarRight;
      // 工具卡：DSH 会把工具名映射为 mcp__<serverName>__<rawName>，两个 key 都注册，
      // 兼容 DSH 与其他 MCP 客户端/未来适配层。
      const keys = [
        ...TOOL_KEYS,
        ...TOOL_KEYS.map((key) => `mcp__sanitized-data__${key}`),
      ];
      for (const key of keys) {
        ctx.effect(() => ctx.slots.inject('tool.call.toolview', () => ctx.slots.register({
          name: 'tool.call.toolview', key,
        }, ToolCard)), `sanitize-detect: tool card ${key}`);
      }
      // 右侧面板类型 + body
      ctx.effect(() => ctx.sidebarRightTabs.register({
        id: PANEL_ID, kind: PANEL_KIND, title: () => '脱敏检测', keepMounted: true,
      }), 'sanitize-detect: panel type');
      ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab', key: PANEL_ID,
      }, DetectPanel)), 'sanitize-detect: panel body');
      // 侧边栏底部入口
      ctx.effect(() => ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
        name: 'sidebar.footer.action', id: 'blctek-sanitize-detect-entry',
      }, PanelEntryButton)), 'sanitize-detect: sidebar entry');
    }

    return {
      name: 'sanitize-detect',
      inject: ['slots', 'sidebarRightTabs', 'sidebarRight'],
      apply,
    };
  },
});
