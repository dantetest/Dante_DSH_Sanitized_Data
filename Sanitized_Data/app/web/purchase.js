/* L3 扫码升级（本机购买页）
 *
 * 数据流（只有用户主动操作时才访问商业服务）：
 *   ① 同源 GET /api/license/status            → 机器码 / 当前状态 / commercial_url
 *   ② GET  {commercial_url}/api/catalog       → SKU 卡片
 *   ③ POST {commercial_url}/api/orders        → qr_svg / pay_url / qr_pay_mode / order_no / order_token
 *   ④ 每 2 秒 GET {commercial_url}/api/orders/{order_no}?token=...
 *   ⑤ paid → 同源 POST /api/license/import {content: license.text} → 自动解锁
 *   ⑥ mock_available 时 POST {commercial_url}/api/mock/pay 模拟支付
 *
 * 电脑网站支付扫码模式（qr_pay_mode=2）：不展示服务端自绘二维码，
 * 改为展示「打开支付宝收银台」按钮；二维码由支付宝收银台页面生成。
 * 安全约定：本页不加载任何外部脚本/字体/图片/CDN；除商业服务返回的 qr_svg
 * （契约保证由服务端生成）外，所有动态字符串都先 esc() 或走 textContent；
 * commercial_url / pay_url 只接受 http(s)，避免 javascript: 之类协议注入。
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const POLL_INTERVAL_MS = 2000;            // 每 2 秒查一次订单
  const POLL_TIMEOUT_MS = 10 * 60 * 1000;   // 最多轮询 10 分钟
  const MAX_NET_ERRORS = 5;                 // 连续网络错误上限，超过暂停并给重试

  const state = {
    status: null,
    commercial: '',        // 经校验的 http(s) 基址；空 = 未配置
    catalog: null,
    sku: '',
    order: null,           // {order_no, order_token, sku, amount_cents, expires_at, qr_svg, pay_url, qr_pay_mode, mock_available, subject}
    licenseText: '',
    polling: false,
    creating: false,
    pollTimer: null,
    tickTimer: null,
    pollDeadline: 0,
    orderExpires: 0,
    netErrors: 0,
  };

  const STATE_TEXT = { free: '未授权', trial: '试用中', paid: '已授权', grace: '宽限期' };
  const FEATURE_TEXT = {
    all_packs: '全部行业规则包',
    custom_packs: '自定义规则包',
    tif: 'TIF / 三维格式',
    d3d: 'TIF / 三维格式',
    ocr: '扫描件 OCR 不限页',
    mcp: 'MCP 接入',
    batch: '批量处理',
    audit_export: '审计 / 复检报告导出',
  };
  const TERM_TEXT = { annual: '/ 年', perpetual: '/ 永久', monthly: '/ 月' };
  const SKU_NAME = { personal: '个人版·年付', personal_perpetual: '个人版·买断',
                     pro: '专业版', team: '团队版', org: '机构版' };

  // ── 基础工具 ──────────────────────────────────────────────

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function fmtYuan(cents) {
    const n = Number(cents);
    if (!Number.isFinite(n)) return '—';
    const yuan = n / 100;
    return '¥' + (Number.isInteger(yuan) ? String(yuan) : yuan.toFixed(2));
  }

  function fmtDate(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString('zh-CN', { hour12: false });
  }

  function fmtDuration(ms) {
    const total = Math.max(0, Math.ceil(Number(ms) / 1000));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function show(el, visible) {
    if (!el) return;
    el.classList.toggle('hidden', !visible);
  }

  function setMsg(elOrId, text, kind) {
    const el = typeof elOrId === 'string' ? $(elOrId) : elOrId;
    if (!el) return;
    el.textContent = text || '';
    el.className = 'msg' + (kind ? ' ' + kind : '');
  }

  /** 只接受 http(s) 绝对地址；返回去掉尾部斜杠的字符串，否则空串。 */
  function safeHttpUrl(raw) {
    const text = String(raw == null ? '' : raw).trim();
    if (!text) return '';
    try {
      const parsed = new URL(text);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
    } catch (err) {
      return '';
    }
    return text.replace(/\/+$/, '');
  }

  function machineValue() {
    const s = state.status || {};
    return String(s.machine_display || s.machine_code || '').trim();
  }

  function commercialBase() {
    return safeHttpUrl(state.commercial);
  }

  function readQuery(name) {
    try {
      return new URLSearchParams(window.location.search).get(name) || '';
    } catch (err) {
      return '';
    }
  }

  function cssAttrEscape(value) {
    return String(value).replace(/[\\"]/g, '\\$&');
  }

  function bindClick(id, fn) {
    const el = $(id);
    if (el) el.addEventListener('click', fn);
  }

  // ── HTTP ─────────────────────────────────────────────────

  async function readJson(res) {
    try { return await res.json(); } catch (err) { return null; }
  }

  async function sameOriginApi(path, options) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, options || {}));
    const data = await readJson(res);
    if (!res.ok) {
      const error = new Error((data && data.error && data.error.message)
        || ('请求失败（HTTP ' + res.status + '）'));
      error.code = data && data.error && data.error.code;
      error.status = res.status;
      throw error;
    }
    return data || {};
  }

  async function commercialApi(path, options) {
    const base = commercialBase();
    if (!base) throw new Error('购买入口尚未配置');
    const res = await fetch(base + path, Object.assign({ cache: 'no-store' }, options || {}));
    const data = await readJson(res);
    if (!res.ok) {
      const error = new Error((data && data.error && data.error.message)
        || ('商业服务返回 HTTP ' + res.status));
      error.code = data && data.error && data.error.code;
      error.status = res.status;
      throw error;
    }
    return data || {};
  }

  // ── 本机授权状态 ─────────────────────────────────────────

  async function loadStatus() {
    try {
      const status = await sameOriginApi('/api/license/status');
      renderStatus(status);
      state.commercial = safeHttpUrl(status.commercial_url);
      setMsg('statusMsg', '', '');
    } catch (err) {
      setMsg('statusMsg', '无法读取本机授权状态：' + err.message, 'err');
      state.commercial = '';
    }
    syncFlow();
  }

  function renderStatus(status) {
    state.status = status || {};
    const s = state.status;
    const st = s.state || 'free';
    const badge = $('stateBadge');
    const known = Object.prototype.hasOwnProperty.call(STATE_TEXT, st);
    badge.textContent = STATE_TEXT[st] || st;
    badge.className = 'badge ' + (known ? st : 'free');
    $('edition').textContent = s.edition || '—';
    $('expiresText').textContent = s.license && s.license.expires_at
      ? fmtDate(s.license.expires_at) : '—';
    $('machineCode').textContent = s.machine_display || s.machine_code || '—';
    $('stateText').textContent = st === 'paid' ? '离线授权有效'
      : st === 'trial' ? '全功能试用中'
      : st === 'grace' ? '已到期，宽限期内仍可使用'
      : '未授权';
    // paid 状态由 applyPaidView 统一隐藏购买入口；paidHint 只给宽限期提示续费。
    show($('paidHint'), st === 'grace');
  }

  function isPaid() {
    return (state.status || {}).state === 'paid';
  }

  function setPriceEntryVisible(visible) {
    document.querySelectorAll('.topnav a, .footer a').forEach((a) => {
      if ((a.textContent || '').trim() === '价格') {
        a.style.display = visible ? '' : 'none';
      }
    });
  }

  /** 已购买用户：隐藏所有购买入口，只保留当前授权卡和操作按钮。 */
  function applyPaidView(paid) {
    if (!paid) return;
    ['shopPanel', 'orderPanel', 'manualPanel', 'noCommercial', 'recentOrderPanel'].forEach((id) => {
      show($(id), false);
    });
    show($('paidHint'), false);
    show($('paidNote'), true);
    show($('paidActions'), true);
    setPriceEntryVisible(false);

    const eyebrow = document.querySelector('.hero .eyebrow');
    const h1 = document.querySelector('.hero h1');
    const lead = document.querySelector('.hero .lead');
    if (eyebrow) eyebrow.textContent = 'PURCHASE · 已授权';
    if (h1) h1.textContent = '授权已生效';
    if (lead) {
      lead.textContent = '本机已解锁，无需再次购买；下面可以查看当前授权状态。'
        + '如需换机或续费，请联系交付顾问。';
    }
  }

  function syncFlow() {
    if (isPaid()) {
      applyPaidView(true);
      return;
    }
    const base = commercialBase();
    show($('noCommercial'), !base);
    show($('shopPanel'), !!base);
    if (!base) {
      renderFallback();
      return;
    }
    if (!state.catalog) loadCatalog();
  }

  /** commercial_url 未配置：显示「购买入口尚未配置」+ 官网链接/人工联系兜底。 */
  function renderFallback() {
    const link = $('fallbackBuy');
    const url = safeHttpUrl(state.status && state.status.purchase_url);
    if (url) {
      const qs = new URLSearchParams({
        sku: 'personal',
        machine_code: machineValue(),
        from: 'purchase',
      });
      link.href = url + (url.includes('?') ? '&' : '?') + qs.toString();
      show(link, true);
      $('fallbackHint').textContent = '也可以前往官网购买（已带本机识别码），购买后把 license.json 在授权页导入即可解锁。';
    } else {
      link.removeAttribute('href');
      show(link, false);
      $('fallbackHint').textContent = '可联系交付顾问人工开通：把上方「本机识别码」发给顾问，收到授权文件后在授权页导入即可解锁。';
    }
  }

  // ── SKU 卡片 ─────────────────────────────────────────────

  async function loadCatalog() {
    const list = $('skuList');
    list.innerHTML = '<div class="loading">正在加载版本与价格…</div>';
    setMsg('catalogMsg', '', '');
    try {
      const data = await commercialApi('/api/catalog', { method: 'GET' });
      state.catalog = data;
      renderCatalog(data);
    } catch (err) {
      state.catalog = null;
      list.innerHTML = '<div class="loading">版本与价格加载失败：' + esc(err.message) + '</div>'
        + '<div class="copyrow" style="margin-top:10px">'
        + '<button class="btn ghost" type="button" data-action="reload-catalog">重试</button></div>';
      setMsg('catalogMsg', '无法连接商业服务：' + err.message, 'err');
    }
  }

  function renderCatalog(data) {
    const list = $('skuList');
    const skus = data && Array.isArray(data.skus) ? data.skus : [];
    if (!skus.length) {
      list.innerHTML = '<div class="loading">当前没有可在线购买的版本，请联系交付顾问。</div>';
      return;
    }
    list.innerHTML = skus.map(skuCardHtml).join('');
    // 支持 /purchase?sku=pro 预选（从价格页跳转过来）：只高亮，不自动下单
    const preselect = readQuery('sku');
    if (preselect) selectSku(preselect, { scroll: true });
  }

  function featureLabels(features) {
    const list = Array.isArray(features) ? features : [];
    const labels = list.map((f) => FEATURE_TEXT[String(f)] || String(f));
    return [...new Set(labels)];
  }

  function skuCardHtml(sku) {
    const item = sku || {};
    const id = String(item.id || '').trim();
    const name = String(item.name || SKU_NAME[id] || id || '版本');
    const edition = String(item.edition || name);
    const term = String(item.term_label || '')
      || TERM_TEXT[String(item.term || '')]
      || (item.term ? '/ ' + String(item.term) : '');
    const devices = Number(item.devices);
    const devicesText = Number.isFinite(devices) && devices > 0
      ? devices + ' 台设备' : '';
    const features = featureLabels(item.features);
    const selfServe = id === 'personal' || id === 'personal_perpetual' || id === 'pro';
    const renewalNote = Number(item.renewal_price_cents) > 0
      ? '<p class="sku-note">续费 ' + esc(fmtYuan(item.renewal_price_cents)) + ' / 年</p>'
      : '';
    const action = selfServe
      ? '<button class="btn seal" type="button" data-action="buy" data-sku="'
        + esc(id) + '">支付宝扫码</button>'
      : '<a class="btn ghost" href="/pricing">联系人工开通</a>';
    return '<article class="sku-card" data-sku-card="' + esc(id) + '">'
      + '<div class="sku-head"><h3>' + esc(edition) + '</h3>'
      + (devicesText ? '<span class="sku-devices">' + esc(devicesText) + '</span>' : '')
      + '</div>'
      + '<div class="sku-price"><b>' + esc(fmtYuan(item.price_cents)) + '</b>'
      + '<span>' + esc(term) + '</span></div>'
      + renewalNote
      + (name && name !== edition ? '<p class="sku-note">' + esc(name) + '</p>' : '')
      + '<ul class="sku-features">'
      + (features.length
        ? features.map((f) => '<li>' + esc(f) + '</li>').join('')
        : '<li>权益以商业服务说明为准</li>')
      + '</ul>' + action + '</article>';
  }

  function selectSku(skuId, opts) {
    const options = opts || {};
    const id = String(skuId || '').trim();
    if (!id) return;
    state.sku = id;
    document.querySelectorAll('[data-sku-card]').forEach((card) => {
      card.classList.toggle('selected', card.getAttribute('data-sku-card') === id);
    });
    if (options.scroll) {
      const card = document.querySelector('[data-sku-card="' + cssAttrEscape(id) + '"]');
      if (card && card.scrollIntoView) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }

  // ── 下单 ─────────────────────────────────────────────────

  async function buySku(skuId) {
    const id = String(skuId || '').trim();
    if (!id || state.creating) return;
    selectSku(id);
    state.sku = id;
    setMsg('catalogMsg', '', '');
    await createOrder(id);
  }

  async function createOrder(skuId) {
    if (state.creating) return;
    if (!commercialBase()) {
      setMsg('catalogMsg', '购买入口尚未配置', 'err');
      return;
    }
    const machine = machineValue();
    if (!machine) {
      setMsg('catalogMsg', '读取不到本机识别码，请刷新页面或到授权页重试。', 'err');
      return;
    }
    state.creating = true;
    setBuyDisabled(true);
    stopPolling();
    resetOrderUi();
    show($('orderPanel'), true);
    show($('btnRetryOrder'), false);
    show($('btnResumePoll'), false);
    $('orderStatus').textContent = '创建中…';
    setMsg('orderMsg', '正在创建支付宝订单…', '');
    try {
      const data = await commercialApi('/api/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku: skuId, machine_code: machine, channel: 'alipay' }),
      });
      const orderNo = String((data && data.order_no) || '').trim();
      const token = String((data && data.order_token) || '').trim();
      if (!orderNo || !token) throw new Error('订单响应缺少 order_no / order_token');
      state.order = {
        order_no: orderNo,
        order_token: token,
        sku: String(data.sku || skuId),
        subject: String(data.subject || ''),
        amount_cents: data.amount_cents,
        qr_svg: typeof data.qr_svg === 'string' ? data.qr_svg : '',
        pay_url: safeHttpUrl(data.pay_url),
        qr_pay_mode: String(data.qr_pay_mode || ''),
        expires_at: String(data.expires_at || ''),
        mock_available: !!data.mock_available,
      };
      saveRecentOrder(state.order);
      renderRecentOrder();
      state.licenseText = '';
      renderOrder();
      startPolling();
    } catch (err) {
      $('orderStatus').textContent = '下单失败';
      setMsg('orderMsg', orderErrorText(err), 'err');
      show($('btnRetryOrder'), true);
    } finally {
      state.creating = false;
      setBuyDisabled(false);
    }
  }

  function orderErrorText(err) {
    const message = (err && err.message) ? err.message : '未知错误';
    const code = err && err.code;
    if (code === 'machine_code_invalid') return '本机识别码格式不被商业服务接受：' + message;
    if (code === 'channel_unavailable') return '支付宝扫码通道暂不可用，请稍后再试或联系交付顾问。';
    if (code === 'rate_limited') return '下单太频繁，请稍等片刻再试。';
    if (code === 'sku_invalid') return '该版本暂不支持在线购买，请选择其他版本。';
    if (code === 'payment_create_failed') return '支付宝下单失败：' + message;
    return '下单失败：' + message;
  }

  function setBuyDisabled(disabled) {
    document.querySelectorAll('[data-action="buy"]').forEach((btn) => {
      btn.disabled = !!disabled;
    });
  }

  function resetOrderUi() {
    state.order = null;
    state.netErrors = 0;
    show($('successPanel'), false);
    $('orderNo').textContent = '—';
    $('orderAmount').textContent = '—';
    $('orderSku').textContent = '—';
    $('orderCountdown').textContent = '—';
    $('orderStatus').textContent = '—';
    $('qrBox').innerHTML = '<div class="qr-placeholder">订单生成后，点击下方按钮打开支付宝收银台</div>';
    const payLink = $('payUrlLink');
    payLink.removeAttribute('href');
    payLink.className = 'btn seal';
    payLink.textContent = '打开支付宝收银台';
    show(payLink, false);
    show($('btnMockPay'), false);
  }

  function renderOrder() {
    const order = state.order;
    if (!order) return;
    $('orderSku').textContent = order.subject || SKU_NAME[order.sku] || order.sku || '—';
    $('orderAmount').textContent = fmtYuan(order.amount_cents);
    $('orderNo').textContent = order.order_no;
    $('orderStatus').textContent = '等待支付';
    const alipayPageMode = String(order.qr_pay_mode || '') === '2';
    const payLink = $('payUrlLink');
    if (alipayPageMode) {
      // 安全优先：二维码只由支付宝收银台生成，本页不再自绘二维码。
      $('qrBox').innerHTML = '';
      const placeholder = document.createElement('div');
      placeholder.className = 'qr-placeholder';
      placeholder.textContent = '请点击下方「打开支付宝收银台」，用手机支付宝扫码支付';
      $('qrBox').appendChild(placeholder);
      $('qrHint').textContent = '支付宝页面会显示二维码；支付成功后本页自动查询并解锁。';
      payLink.className = 'btn seal';
      payLink.textContent = '打开支付宝收银台';
    } else {
      // 兼容旧通道（mock / 其他二维码通道）：才使用服务端返回的 qr_svg。
      if (order.qr_svg) {
        $('qrBox').innerHTML = order.qr_svg;
      } else {
        $('qrBox').innerHTML = '';
        const placeholder = document.createElement('div');
        placeholder.className = 'qr-placeholder';
        placeholder.textContent = '二维码暂不可用，请使用下方按钮继续支付。';
        $('qrBox').appendChild(placeholder);
      }
      $('qrHint').textContent = '请用手机支付宝扫码支付';
      payLink.className = 'btn ghost';
      payLink.textContent = '在浏览器打开支付页';
    }
    if (order.pay_url) {
      payLink.href = order.pay_url;
      show(payLink, true);
    } else {
      payLink.removeAttribute('href');
      show(payLink, false);
    }
    show($('btnMockPay'), !!order.mock_available);
    show($('orderPanel'), true);
  }

  // ── 轮询订单（2 秒一次，最多 10 分钟） ───────────────────

  function startPolling() {
    stopPolling();
    const order = state.order;
    if (!order) return;
    state.polling = true;
    state.netErrors = 0;
    const now = Date.now();
    state.pollDeadline = now + POLL_TIMEOUT_MS;
    const expires = Date.parse(order.expires_at || '');
    state.orderExpires = Number.isFinite(expires) ? expires : now + 15 * 60 * 1000;
    show($('btnResumePoll'), false);
    show($('btnRetryOrder'), false);
    setMsg('orderMsg', '订单已创建，正在等待支付（每 2 秒自动查询一次）…', '');
    updateCountdown();
    state.tickTimer = window.setInterval(updateCountdown, 1000);
    state.pollTimer = window.setTimeout(pollTick, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    state.polling = false;
    if (state.pollTimer) { window.clearTimeout(state.pollTimer); state.pollTimer = null; }
    if (state.tickTimer) { window.clearInterval(state.tickTimer); state.tickTimer = null; }
  }

  function schedulePoll(delay) {
    if (!state.polling) return;
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    state.pollTimer = window.setTimeout(pollTick,
      typeof delay === 'number' ? delay : POLL_INTERVAL_MS);
  }

  async function pollTick() {
    if (!state.polling || !state.order) return;
    if (Date.now() > state.pollDeadline) { onPollTimeout(); return; }
    const order = state.order;
    const url = '/api/orders/' + encodeURIComponent(order.order_no)
      + '?token=' + encodeURIComponent(order.order_token);
    try {
      const data = await commercialApi(url, { method: 'GET' });
      state.netErrors = 0;
      const status = String((data && data.status) || 'pending');
      if (status === 'paid') {
        stopPolling();
        await onPaid(data);
        return;
      }
      if (status === 'expired' || status === 'closed') {
        stopPolling();
        onOrderEnded(status);
        return;
      }
      $('orderStatus').textContent = '等待支付';
      setMsg('orderMsg', '等待支付确认…（每 2 秒查询一次，最多查询 10 分钟）', '');
      schedulePoll();
    } catch (err) {
      if (err && err.status === 404) {
        stopPolling();
        $('orderStatus').textContent = '订单不存在';
        setMsg('orderMsg', '订单查询失败（' + (err.message || '订单不存在或 token 已失效')
          + '），请点「重新下单」。', 'err');
        show($('btnRetryOrder'), true);
        return;
      }
      state.netErrors += 1;
      if (state.netErrors >= MAX_NET_ERRORS) {
        stopPolling();
        setMsg('orderMsg', '网络异常，已暂停自动查询：' + err.message
          + '。可点「继续查询」重试。', 'err');
        show($('btnResumePoll'), true);
        return;
      }
      setMsg('orderMsg', '网络波动，正在重试（' + state.netErrors + '/' + MAX_NET_ERRORS
        + '）：' + err.message, 'err');
      schedulePoll();
    }
  }

  function updateCountdown() {
    if (!state.order) return;
    const left = state.orderExpires - Date.now();
    $('orderCountdown').textContent = left > 0 ? fmtDuration(left) : '已过期';
  }

  function onPollTimeout() {
    stopPolling();
    $('orderStatus').textContent = '查询超时';
    setMsg('orderMsg', '已连续查询 10 分钟仍未确认支付。若刚完成支付，可点「继续查询」；'
      + '也可稍后在授权页手动导入，或凭订单号联系交付顾问。', 'err');
    show($('btnResumePoll'), true);
  }

  function onOrderEnded(status) {
    $('orderStatus').textContent = status === 'expired' ? '订单已过期' : '订单已关闭';
    setMsg('orderMsg', status === 'expired'
      ? '订单已过期，未完成支付。请点「重新下单」再试。'
      : '订单已关闭。请点「重新下单」再试。', 'err');
    show($('btnResumePoll'), false);
    show($('btnRetryOrder'), true);
  }

  // ── 支付成功 → 自动导入授权 ─────────────────────────────

  async function onPaid(data) {
    stopPolling();
    $('orderStatus').textContent = '已支付';
    setMsg('orderMsg', '支付成功，正在自动导入授权…', 'ok');
    const license = data && data.license;
    const text = license && typeof license.text === 'string' ? license.text : '';
    if (!text) {
      setMsg('orderMsg', '已支付，但订单响应没有授权内容。请凭订单号 '
        + (state.order ? state.order.order_no : '')
        + ' 联系交付顾问，或稍后点「继续查询」。', 'err');
      show($('btnResumePoll'), true);
      return;
    }
    state.licenseText = text;
    setManualText(text);
    try {
      const result = await sameOriginApi('/api/license/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text }),
      });
      renderSuccess(result);
      await loadStatus();              // 刷新当前授权卡片（成功面板保持显示）
      show($('manualPanel'), false);
    } catch (err) {
      setMsg('orderMsg', '自动导入失败：' + err.message + '。请复制下方授权码手动导入。', 'err');
      showManual(text, '自动导入失败：' + err.message);
    }
  }

  function renderSuccess(result) {
    const info = result || {};
    const license = info.license || {};
    const edition = info.edition || license.edition || info.sku || '已授权';
    const expires = license.expires_at || '';
    show($('successPanel'), true);
    show($('orderPanel'), false);
    $('successLine').textContent = expires
      ? '已解锁 · ' + String(edition) + ' · 到期 ' + fmtDate(expires)
      : '已解锁 · ' + String(edition) + '（永久）';
    $('successEdition').textContent = edition;
    $('successExpires').textContent = expires ? fmtDate(expires) : '—';
    if ($('successPanel').scrollIntoView) {
      $('successPanel').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  // ── 手动导入兜底 ─────────────────────────────────────────

  function setManualText(text) {
    if ($('manualText')) $('manualText').value = String(text == null ? '' : text);
  }

  function showManual(text, message) {
    show($('manualPanel'), true);
    if (typeof text === 'string') setManualText(text);
    setMsg('manualMsg', message || '', 'err');
    if ($('manualPanel').scrollIntoView) {
      $('manualPanel').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  async function manualImport() {
    const content = String($('manualText').value || '').trim();
    if (!content) {
      setMsg('manualMsg', '请先粘贴授权文本。', 'err');
      return;
    }
    setMsg('manualMsg', '正在校验签名和机器绑定…', '');
    try {
      const result = await sameOriginApi('/api/license/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: content }),
      });
      renderSuccess(result);
      await loadStatus();
      show($('manualPanel'), false);
      setMsg('orderMsg', '', '');
    } catch (err) {
      setMsg('manualMsg', '导入失败：' + err.message, 'err');
    }
  }

  async function copyLicense() {
    const text = String($('manualText').value || '');
    if (!text) {
      setMsg('manualMsg', '没有可复制的授权码。', 'err');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setMsg('manualMsg', '授权码已复制。', 'ok');
    } catch (err) {
      $('manualText').focus();
      $('manualText').select();
      setMsg('manualMsg', '已选中授权码，请按 Ctrl+C 复制。', 'ok');
    }
  }

  // ── 演示模式：模拟支付 ───────────────────────────────────

  async function mockPay() {
    const order = state.order;
    if (!order || !commercialBase()) return;
    const btn = $('btnMockPay');
    btn.disabled = true;
    setMsg('orderMsg', '正在模拟支付…', '');
    try {
      const data = await commercialApi('/api/mock/pay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order_no: order.order_no, token: order.order_token }),
      });
      if (data && data.license && typeof data.license.text === 'string' && data.license.text) {
        await onPaid(data);
        return;
      }
      setMsg('orderMsg', '已模拟支付，正在等待授权签发…', 'ok');
      $('orderStatus').textContent = '已支付';
      schedulePoll(300);
    } catch (err) {
      setMsg('orderMsg', '模拟支付失败：' + err.message, 'err');
    } finally {
      btn.disabled = false;
    }
  }

  // ── 暂停/继续/取消 ──────────────────────────────────────

  function resumePoll() {
    if (!state.order) {
      setMsg('orderMsg', '没有可继续查询的订单，请重新下单。', 'err');
      return;
    }
    show($('btnResumePoll'), false);
    startPolling();
  }

  function cancelPolling() {
    if (!state.order) return;
    stopPolling();
    show($('btnResumePoll'), true);
    $('orderStatus').textContent = '已暂停';
    setMsg('orderMsg', '已停止自动查询。若已完成支付，可点「继续查询」取回授权；'
      + '也可使用下方手动导入。', '');
  }

  // ── 最近订单：找回授权（仅本机浏览器 localStorage） ─────────
  //
  // 客户支付成功但自动导入失败、或者关闭页面后，可凭本机保存的订单号+查询令牌
  // 打开商业服务的订单页查看/复制授权码。令牌等同取货凭证，不写日志、不外发。

  const RECENT_ORDER_KEY = 'yz_last_order_v1';

  function readRecentOrder() {
    try {
      const raw = window.localStorage.getItem(RECENT_ORDER_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || typeof data !== 'object') return null;
      if (!data.order_no || !data.order_token || !safeHttpUrl(data.commercial)) return null;
      return data;
    } catch (err) {
      return null;
    }
  }

  function saveRecentOrder(order) {
    const commercial = commercialBase();
    if (!commercial || !order || !order.order_no || !order.order_token) return;
    try {
      window.localStorage.setItem(RECENT_ORDER_KEY, JSON.stringify({
        commercial,
        order_no: order.order_no,
        order_token: order.order_token,
        subject: order.subject || '',
        amount_cents: order.amount_cents,
        created_at: Date.now(),
      }));
    } catch (err) {
      // 隐私模式/存储被禁用时不影响支付主流程
    }
  }

  function recentOrderUrl(record) {
    const base = safeHttpUrl(record && record.commercial);
    if (!base || !record.order_no || !record.order_token) return '';
    return base + '/order.html?order_no=' + encodeURIComponent(record.order_no)
      + '&token=' + encodeURIComponent(record.order_token);
  }

  function renderRecentOrder() {
    const record = readRecentOrder();
    const panel = $('recentOrderPanel');
    if (!panel) return;
    if (!record) {
      show(panel, false);
      return;
    }
    const url = recentOrderUrl(record);
    if (!url) {
      show(panel, false);
      return;
    }
    $('recentOrderNo').textContent = record.order_no;
    $('recentOrderTime').textContent = record.created_at ? fmtDate(record.created_at) : '—';
    $('recentOrderLink').href = url;
    setMsg('recentOrderMsg', '', '');
    show(panel, true);
  }

  async function copyRecentOrderLink() {
    const record = readRecentOrder();
    const url = recentOrderUrl(record);
    if (!url) {
      setMsg('recentOrderMsg', '没有可复制的订单链接。', 'err');
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      setMsg('recentOrderMsg', '订单链接已复制，请妥善保存（含查询令牌，勿转发）。', 'ok');
    } catch (err) {
      try {
        const input = document.createElement('textarea');
        input.value = url;
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
        setMsg('recentOrderMsg', '订单链接已复制，请妥善保存（含查询令牌，勿转发）。', 'ok');
      } catch (fallbackErr) {
        setMsg('recentOrderMsg', '复制失败，请打开订单页后手动保存地址。', 'err');
      }
    }
  }

  function forgetRecentOrder() {
    try {
      window.localStorage.removeItem(RECENT_ORDER_KEY);
    } catch (err) {
      // ignore
    }
    show($('recentOrderPanel'), false);
  }

  // ── 绑定 ────────────────────────────────────────────────

  function bind() {
    const list = $('skuList');
    if (list) {
      list.addEventListener('click', (ev) => {
        const target = ev.target;
        if (!target || !target.closest) return;
        if (target.closest('[data-action="reload-catalog"]')) {
          state.catalog = null;
          loadCatalog();
          return;
        }
        const buy = target.closest('[data-action="buy"]');
        if (buy) buySku(buy.getAttribute('data-sku'));
      });
    }
    bindClick('btnMockPay', mockPay);
    bindClick('btnCancelOrder', cancelPolling);
    bindClick('btnResumePoll', resumePoll);
    bindClick('btnRetryOrder', () => {
      show($('btnRetryOrder'), false);
      createOrder(state.sku || 'personal');
    });
    bindClick('btnManualImport', manualImport);
    bindClick('btnCopyLicense', copyLicense);
    bindClick('btnCopyOrderLink', copyRecentOrderLink);
    bindClick('btnForgetOrder', forgetRecentOrder);
    // 页面卸载/进入往返缓存时停止轮询，避免后台空转
    window.addEventListener('beforeunload', stopPolling);
    window.addEventListener('pagehide', stopPolling);
  }

  bind();
  renderRecentOrder();
  loadStatus();
})();
