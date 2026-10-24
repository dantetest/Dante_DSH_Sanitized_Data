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
    const TOOL_KEYS = [
      'sanitize_document', 'redact_text', 'sanitize_file', 'get_sanitize_job',
      'list_sanitize_jobs', 'get_sanitize_entities', 'update_sanitize_entity',
      'mark_sanitize_region', 'accept_all_sanitize_entities', 'export_sanitize_job',
      'get_sanitize_audit', 'rehydrate_sanitize_text', 'open_sanitize_workbench',
    ];

    // 工具卡完成后把最近 job 留在 footer，避免 DSH 折叠过程后看不到结果。
    const recentState = { value: null, listeners: new Set() };
    function setRecent(value) {
      const next = value && value.jobId ? value : null;
      const prev = recentState.value;
      if (prev && next && prev.jobId === next.jobId && prev.total === next.total && prev.summary === next.summary) return;
      recentState.value = next;
      for (const fn of [...recentState.listeners]) {
        try { fn(next); } catch { /* ignore listener failure */ }
      }
    }
    function subscribeRecent(fn) {
      recentState.listeners.add(fn);
      return () => recentState.listeners.delete(fn);
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
      const imageRefs = phase === 'result' && block && Array.isArray(block.content)
        ? block.content.filter((part) => part && part.type === 'image' &&
            part.attachment && typeof part.attachment.attachmentId === 'string' &&
            part.attachment.attachmentId)
        : [];

      function openPanel() {
        if (!jobId) return;
        const url = workbench || `http://127.0.0.1:8760/console?job=${encodeURIComponent(jobId)}`;
        // 优先使用 DSH 原生右侧浏览器 tab，直接显示真实工作台（不是仿制面板）。
        if (openWorkbenchTab(url)) return;
        window.open(url, '_blank', 'noopener');
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

      React.useEffect(() => {
        if (phase !== 'result' || !jobId) return;
        const total = payload && payload.counts ? payload.counts.total : null;
        setRecent({ jobId, total, summary, url: workbench || null });
      }, [phase, jobId, summary, workbench]);

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
        imageRefs.length > 0 && props.renderSlot && props.loadImage && props.renderSlot('tool.call.images', {
          images: imageRefs.map((part) => ({ attachment: part.attachment })),
          loadImage: props.loadImage,
          align: 'start',
        }),
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
        let healthOk = false;
        try {
          const health = await fetch(PREFIX + '/health', { headers: { accept: 'application/json' } });
          healthOk = health.ok;
        } catch {
          healthOk = false;
        }
        if (!healthOk) {
          // 宿主插件尝试在本机拉起工作台（白名单路径，仅 127.0.0.1）。
          try { await fetch(PREFIX + '/start', { method: 'POST' }); } catch { /* 启动失败由用户手工启动 */ }
        }
        // 左下角入口默认打开空白工作台；不要自动挑「最近一个 job」，
        // 否则会把上一次测试/样例任务（含虚构数据）当成用户工作台展示。
        // 真实工具结果仍通过 recent.url / recent.jobId 打开对应深链。
        return base + '/console';
      } catch {
        return base + '/console';
      }
    }

    function PanelEntryButton() {
      const [recent, setRecentView] = React.useState(recentState.value);
      React.useEffect(() => subscribeRecent(setRecentView), []);
      const open = async () => {
        let url = recent && recent.url;
        if (!url && recent && recent.jobId) {
          url = 'http://127.0.0.1:8760/console?job=' + encodeURIComponent(recent.jobId);
        }
        if (!url) url = await latestWorkbenchUrl();
        if (openWorkbenchTab(url)) return;
        window.open(url, '_blank', 'noopener');
      };
      const label = recent && Number.isInteger(recent.total) ? '数据隐站工作台 · ' + recent.total : '数据隐站工作台';
      return h('button', {
        type: 'button', onClick: open,
        title: recent && recent.jobId ? '数据隐站工作台 · ' + recent.jobId : '数据隐站工作台',
        style: {
          appearance: 'none', border: 'none', background: 'transparent', color: 'inherit',
          cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '4px 6px', borderRadius: 6, fontSize: 12,
        },
      }, h('span', {
        style: { width: 18, height: 18, borderRadius: 5,
          background: recent ? COLORS.greenBg : COLORS.blueBg,
          color: recent ? COLORS.green : COLORS.blue,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700 },
      }, '隐'), label);
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
          children: { 'tool.call.images': { kind: 'single', scope: 'session' } },
        }, ToolCard)), `sanitize-detect: tool card ${key}`);
      }
      // T2 默认由 DSH 原生 browser tab 打开真实工作台；不再注册仿制面板。
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
