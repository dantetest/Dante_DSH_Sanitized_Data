/* W3 授权页：只调用同源 /api/license/*，不加载外部资源 */
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { status: null };

  const TEXT = {
    free: '免费版',
    trial: '试用中',
    paid: '已授权',
    grace: '宽限期',
  };

  async function api(path, options = {}) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, options));
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      const err = new Error((data && data.error && data.error.message) || '请求失败');
      err.code = data && data.error && data.error.code;
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function fmtDate(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString('zh-CN', { hour12: false });
  }

  function render(status) {
    state.status = status || {};
    const s = state.status;
    const st = s.state || 'free';
    const testMode = s.paywall_enabled === false
      || (s.quota && s.quota.enforced === false);
    const badge = $('stateBadge');
    badge.textContent = testMode ? '测试版' : (TEXT[st] || st);
    badge.className = 'badge ' + (testMode ? 'paid' : (['free', 'trial', 'paid', 'grace'].includes(st) ? st : 'free'));
    $('edition').textContent = testMode ? '测试版 · 全功能' : (s.edition || '免费版');
    $('stateText').textContent = testMode ? '收费门禁未开启（内部测试）'
      : st === 'paid' ? '离线授权有效'
      : st === 'trial' ? '全功能试用'
      : st === 'grace' ? '已到期，宽限期内仍可使用'
      : '未授权';
    $('expiresText').textContent = s.license && s.license.expires_at
      ? fmtDate(s.license.expires_at) : '—';
    $('machineCode').textContent = s.machine_display || s.machine_code || '—';
    const q = s.quota || {};
    if (testMode) {
      $('quotaText').textContent = '全功能测试，不限制份数/页数';
    } else if (q.unlimited) {
      $('quotaText').textContent = st === 'trial'
        ? '不限份数/页数（试用期内）'
        : '不限份数/页数';
    } else {
      const used = Number(q.used_today || 0);
      const limit = Number(q.limit_today || 3);
      $('quotaText').textContent = `今日剩余 ${Math.max(0, limit - used)}/${limit} 份 · 单份 ≤20 页`;
    }
    const trial = s.trial || {};
    $('trialRow').style.display = (!testMode && st === 'free' && !trial.used) ? 'block' : 'none';
    if (s.clock_suspect) {
      $('statusMsg').textContent = '系统时间异常：本机授权状态可能受影响，请校正系统时间。';
      $('statusMsg').className = 'msg err';
    }
    const buy = $('buyLink');
    const url = s.purchase_url || '';
    if (/^https?:\/\//i.test(url)) {
      const qs = new URLSearchParams({
        sku: 'personal',
        machine_code: s.machine_display || s.machine_code || '',
        from: 'license',
      });
      buy.href = url + (url.includes('?') ? '&' : '?') + qs.toString();
      buy.target = '_blank';
      buy.rel = 'noopener';
      $('buyHint').textContent = '将在系统浏览器打开官网购买页，并带上本机识别码；本机文件不会上传。';
    } else {
      buy.href = '/pricing';
      $('buyHint').textContent = '购买入口尚未配置；可先查看价格页，或联系交付顾问人工开通。';
    }
    // L3 扫码升级：本机购买页内完成支付宝支付 → 自动导入授权。
    // 商业服务未配置时购买页会显示兜底（官网链接 / 人工联系），入口仍然可用。
    const commercial = String(s.commercial_url || '').trim().replace(/\/+$/, '');
    const hasCommercial = /^https?:\/\//i.test(commercial);
    const scan = $('scanBuyLink');
    if (scan) scan.href = hasCommercial ? '/purchase?from=license' : '/purchase';
    if ($('scanHint')) {
      $('scanHint').textContent = hasCommercial
        ? '支付宝扫码支付成功后自动导入授权，不需要手动复制；本机文件不会上传。'
        : '扫码升级需要先配置商业服务入口；未配置时请在购买页使用官网链接或联系交付顾问。';
    }

    // 已授权用户不再展示购买/升级入口，避免续费卡片干扰。
    const paid = !testMode && st === 'paid';
    const grid = document.querySelector('.license-grid');
    if (grid) grid.classList.toggle('paid', paid);
    const upgrade = document.querySelector('.license-upgrade');
    if (upgrade) upgrade.style.display = paid ? 'none' : '';
    document.querySelectorAll('.topnav a, .footer a').forEach((a) => {
      if ((a.textContent || '').trim() === '价格') a.style.display = paid ? 'none' : '';
    });
    const statusCard = document.querySelector('.license-status');
    if (statusCard && paid && !statusCard.querySelector('.paid-no-buy-note')) {
      const note = document.createElement('p');
      note.className = 'hint paid-no-buy-note';
      note.style.marginTop = '10px';
      note.textContent = '已购买用户不再显示购买入口；如需换机或续费，请联系交付顾问。';
      statusCard.appendChild(note);
    }
  }

  async function load() {
    try {
      render(await api('/api/license/status'));
      $('statusMsg').textContent = '';
      $('statusMsg').className = 'msg';
    } catch (err) {
      $('statusMsg').textContent = '无法读取授权状态：' + err.message;
      $('statusMsg').className = 'msg err';
    }
    loadEvents();
  }

  async function loadEvents() {
    try {
      const data = await api('/api/license/events?limit=20');
      const list = $('eventList');
      const events = (data && data.events) || [];
      if (!events.length) { list.innerHTML = '<li>暂时没有授权事件</li>'; return; }
      list.innerHTML = events.slice().reverse().map((ev) => {
        const at = fmtDate(ev.at);
        const kind = esc(ev.event || 'event');
        const extra = ev.license_id ? ' · ' + esc(ev.license_id) : '';
        return `<li>${esc(at)} · ${kind}${extra}</li>`;
      }).join('');
    } catch (err) {
      $('eventList').innerHTML = '<li>事件读取失败</li>';
    }
  }

  async function copyMachine() {
    const text = $('machineCode').textContent.trim();
    if (!text || text === '—') return;
    try {
      await navigator.clipboard.writeText(text);
      $('statusMsg').textContent = '本机识别码已复制。';
      $('statusMsg').className = 'msg ok';
    } catch (err) {
      const range = document.createRange();
      range.selectNodeContents($('machineCode'));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      $('statusMsg').textContent = '已选中识别码，请按 Ctrl+C 复制。';
      $('statusMsg').className = 'msg ok';
    }
  }

  async function importLicense(text) {
    const content = String(text || '').trim();
    if (!content) {
      setImportMsg('请选择授权文件或粘贴授权文本。', true);
      return;
    }
    setImportMsg('正在校验签名和机器绑定…', false);
    try {
      const data = await api('/api/license/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      const prev = state.status || {};
      render(Object.assign({}, prev, data, { quota: data.quota || prev.quota }));
      $('verifiedStamp').classList.add('on');
      setImportMsg('导入成功，已核验本机授权。', false);
      $('licenseText').value = '';
    } catch (err) {
      setImportMsg('导入失败：' + err.message, true);
    }
  }

  function setImportMsg(text, isErr) {
    const el = $('importMsg');
    el.textContent = text;
    el.className = 'msg' + (isErr ? ' err' : ' ok');
  }

  async function backupLicense() {
    try {
      const data = await api('/api/license/file');
      const blob = new Blob([data.content || ''], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.filename || 'license.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      setImportMsg('已生成授权备份。', false);
    } catch (err) {
      setImportMsg('没有可备份的授权文件：' + err.message, true);
    }
  }

  async function startTrial() {
    const btn = $('btnStartTrial');
    btn.disabled = true;
    try {
      await api('/api/trial/start', { method: 'POST' });
      await load();
      $('statusMsg').textContent = '试用已开始，14 天内全功能。';
      $('statusMsg').className = 'msg ok';
    } catch (err) {
      $('statusMsg').textContent = err.message || '试用启用失败';
      $('statusMsg').className = 'msg err';
      btn.disabled = false;
    }
  }

  function readFile(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { $('licenseText').value = String(reader.result || ''); };
    reader.onerror = () => setImportMsg('文件读取失败。', true);
    reader.readAsText(file);
  }

  function bind() {
    $('btnCopyMachine').addEventListener('click', copyMachine);
    $('btnImport').addEventListener('click', () => importLicense($('licenseText').value));
    $('btnBackup').addEventListener('click', backupLicense);
    $('btnStartTrial').addEventListener('click', startTrial);
    $('licenseFile').addEventListener('change', (ev) => readFile(ev.target.files && ev.target.files[0]));
    const drop = $('licenseDrop');
    drop.addEventListener('click', () => $('licenseFile').click());
    drop.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); $('licenseFile').click(); }
    });
    ['dragenter', 'dragover'].forEach((name) => drop.addEventListener(name, (ev) => {
      ev.preventDefault(); drop.classList.add('hot');
    }));
    ['dragleave', 'drop'].forEach((name) => drop.addEventListener(name, (ev) => {
      ev.preventDefault(); drop.classList.remove('hot');
    }));
    drop.addEventListener('drop', (ev) => readFile(ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0]));
  }

  bind();
  load();
})();
