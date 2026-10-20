/* ============================================================
   数据隐站 · 控制台脚本
   纯原生 JS，无依赖、无网络外呼；只与本机 /api/* 交互。
   结构：工具 → 状态 → API → 投放 → 识别轮询 → 对照台渲染
        → 清单/确认 → 框选补漏 → 交付 → 启动
   ============================================================ */
'use strict';

/* ─────────────── 小工具与常量 ─────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
const pct = (v) => (v * 100).toFixed(3) + '%';

function mk(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = String(text);
  return node;
}
function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

function fmtSize(value) {
  const n = num(value);
  if (!n || n <= 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

const TYPE_CN = {
  PERSON: '人名', ID_CARD: '身份证', PHONE: '电话', ADDRESS: '地址', ORG: '机构',
  CASE_NO: '案号', AMOUNT: '金额', BANK: '账号', LICENSE: '证照号', EMAIL: '邮箱',
  DATE: '日期', IP: '网络地址',
  // 视觉 / 码类（mediascan 检出；后端 jobs._VISUAL_SPEC 的 etype）
  SEAL: '公章/印章', SIGNATURE: '手写签名', SIG_BLOCK: '签署区', HANDWRITING: '疑似手写',
  FACE: '人脸', ID_DOCUMENT: '证件', PLATE: '车牌', ROAD_SIGN: '路牌',
  DOOR_ADDRESS: '门牌/门址', QRCODE: '二维码', BARCODE: '条形码',
  // 身份证关键字段（方案 A：默认「关键字段」，整卡可选）
  ID_PHOTO: '证件头像', ID_NAME: '证件姓名', ID_NUMBER: '证件号码', ID_ADDRESS: '证件住址',
  // 身份证反面（A 方案扩展）：只遮签发机关 + 有效期限，国徽/标题保留
  ID_AUTHORITY: '证件签发机关', ID_VALIDITY: '证件有效期',
  OTHER: '其他敏感',
};
const TYPE_ORDER = [
  'PERSON', 'ID_CARD', 'ADDRESS', 'PHONE', 'ORG', 'CASE_NO', 'AMOUNT', 'BANK', 'LICENSE', 'EMAIL', 'DATE', 'IP',
  'FACE', 'ID_DOCUMENT', 'ID_PHOTO', 'ID_NAME', 'ID_NUMBER', 'ID_ADDRESS', 'ID_AUTHORITY', 'ID_VALIDITY',
  'PLATE', 'ROAD_SIGN', 'DOOR_ADDRESS',
  'SEAL', 'SIGNATURE', 'SIG_BLOCK', 'HANDWRITING', 'QRCODE', 'BARCODE',
  'OTHER',
];
const TYPE_COLOR = {
  PERSON: '#B23A2E', ID_CARD: '#1A1714', ADDRESS: '#8C6A3F', PHONE: '#4C6B4A',
  ORG: '#6B5B8A', CASE_NO: '#A8763E', AMOUNT: '#3F6B7A', BANK: '#8C5A5A',
  LICENSE: '#6B6B5A', EMAIL: '#7A5A4A', DATE: '#6B6B5A', IP: '#4A5A6B',
  // 视觉 / 码类：沿用纸面档案的 muted 色系（不引入高饱和色），组名仍以中文为准
  SEAL: '#8C2F26', SIGNATURE: '#5E4A66', SIG_BLOCK: '#7A6E5A', HANDWRITING: '#4F6B6B',
  FACE: '#B0573F', ID_DOCUMENT: '#40372F', PLATE: '#46603A', ROAD_SIGN: '#8A6A2F',
  DOOR_ADDRESS: '#6B4636', QRCODE: '#2E3A48', BARCODE: '#24211C',
  // 身份证关键字段：同一份「证件」下的细分，用同族低饱和色区分
  ID_PHOTO: '#8E4A3C', ID_NAME: '#5A4A3F', ID_NUMBER: '#2F4F66', ID_ADDRESS: '#7A6A4A',
  // 身份证反面两字段（A 方案扩展）
  ID_AUTHORITY: '#46604A', ID_VALIDITY: '#5C4A6B',
  OTHER: '#8C8272',
};
const STRICT_CN = { loose: '宽松', standard: '标准', strict: '最严' };
const STAGE_CN = {
  queued: '排队中', upload: '正在接收文件…', upload_received: '文件已收下',
  parse: '正在解封文件', parsing: '正在解封文件', ocr: '正在辨认扫描件',
  detect: '正在逐页识别', detecting: '正在逐页识别', replace: '正在换名与涂黑',
  replacing: '正在换名与涂黑', verify: '正在出口自检', verifying: '正在出口自检',
  export: '正在装订成品', exporting: '正在装订成品', done: '识别完成',
  complete: '识别完成', completed: '识别完成', ready: '识别完成', review: '等你确认',
  finished: '识别完成', error: '出了点问题',
};
const DONE_STAGES = ['done', 'complete', 'completed', 'ready', 'review', 'awaiting_review', 'await_review', 'finished'];
const MENU_ETYPES = [
  { label: '人名', etype: 'PERSON' }, { label: '电话', etype: 'PHONE' },
  { label: '地址', etype: 'ADDRESS' }, { label: '金额', etype: 'AMOUNT' },
  { label: '其他敏感', etype: 'OTHER' },
];

/* 证件脱敏档位（用户已批准方案 A）：默认「关键字段」，整卡涂黑可选。
   fields=遮头像/姓名/身份证号/住址 + 反面签发机关/有效期；whole=整卡覆盖（公开/不确定场景推荐）。
   只影响任务创建（POST /api/jobs 的 id_card_mode），已创建的任务不自动重跑。 */
const ID_MODE_OPTIONS = [
  { value: 'fields', label: '关键字段（默认）', short: '关键字段' },
  { value: 'whole', label: '整卡涂黑', short: '整卡涂黑' },
];
const ID_MODE_CN = { fields: '关键字段', whole: '整卡涂黑' };
const ID_MODE_DESC = {
  fields: '遮头像/姓名/身份证号/住址 + 反面签发机关/有效期',
  whole: '整卡覆盖（公开 / 不确定场景推荐）',
};
function normalizeIdCardMode(value) {
  return value === 'whole' ? 'whole' : 'fields';
}

/* 人话错误：绝不把堆栈、代码原样甩给用户 */
const ERROR_CN = {
  unreachable: '连不上本机的隐站服务，请确认它还在运行。',
  missing_file: '没收到文件，再拖一次试试。',
  upload_too_large: '这个文件太大了，先换个小一点的。',
  parse_failed: '这份文件没能打开，可能是格式不太标准，换一份试试？',
  export_failed: '成品没能生成，原件没有被改动，稍后再试一次。',
  verify_failed: '出口自检没通过，成品已经销毁，原件没有被改动。',
  job_not_found: '这个任务已经不在本机了，请重新投放文件。',
  not_found: '这件事本机还没准备好，请重新试一次。',
  bad_request: '这一步没走通，请重新试一次。',
  bad_response: '本机回了看不懂的内容，请重新试一次。',
  busy: '本机正在忙，等这一份处理完再试。',
  forbidden: '这项操作被拦下了，原件没有被改动。',
};
class AppError extends Error {
  constructor(code, human) { super(human || '这一步没走通，请重新试一次。'); this.code = code; this.human = this.message; }
}
function cleanMessage(text) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  if (!s || /traceback|exception|stack trace|file\s+"|line\s+\d+|\bat\s+0x/i.test(s)) return '';
  return s.length > 120 ? s.slice(0, 120) + '…' : s;
}
function humanMessage(data, status) {
  const err = data && data.error;
  if (err && typeof err.message === 'string') {
    const msg = cleanMessage(err.message);
    if (msg) return msg;
  }
  const code = err && err.code;
  if (code && ERROR_CN[code]) return ERROR_CN[code];
  if (status === 413) return ERROR_CN.upload_too_large;
  if (status === 404) return ERROR_CN.not_found;
  if (status >= 500) return '本机处理时出了点问题，原件没有被改动。';
  return '这一步没走通，请重新试一次。';
}
function jobErrorMessage(error) {
  if (!error) return '处理时出了点问题，原件没有被改动。';
  if (typeof error === 'string') return cleanMessage(error) || '处理时出了点问题，原件没有被改动。';
  if (typeof error === 'object') {
    if (error.code && ERROR_CN[error.code]) return ERROR_CN[error.code];
    const msg = cleanMessage(error.message);
    if (msg) return msg;
  }
  return '处理时出了点问题，原件没有被改动。';
}

/* ─────────────── 状态 ─────────────── */
const state = {
  screen: 'drop',
  health: null,
  uploadId: null,
  fileName: '',
  jobId: null,
  strictness: 'standard',
  idCardMode: 'fields',
  mode: 'sur',
  selectMode: false,
  busy: false,
  job: null,
  counts: { total: 0, done: 0, pending: 0 },
  pages: 1,
  page: 0,
  entities: [],
  previews: { orig: new Map(), out: new Map() },
  entityPage: new Map(),
  pageLoading: null,
  pollTimer: null,
  pollFailures: 0,
  scanTimer: null,
  scanTyping: false,
  scanQueue: [],
  lastStageText: '',
  lastScanPage: 0,
  totalLogged: false,
  typeLogged: false,
  lastTypesKey: '',
  loggedTypes: new Set(),
  typeSummaryLogged: false,
  finishing: false,
  markDraft: null,
  markMenu: null,
  markIdx: 0,
  exported: false,
  modalBusy: false,
  docNo: '',
  cardTpl: null,
  motionReduced: !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches),
  toastTimer: null,
};

const ui = {};

/* ─────────────── 本机 API 封装 ─────────────── */
async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, Object.assign({ cache: 'no-store' }, options));
  } catch (err) {
    throw new AppError('unreachable', ERROR_CN.unreachable);
  }
  let data = null;
  let text = '';
  try { text = await res.text(); } catch (err) { text = ''; }
  if (text) {
    try { data = JSON.parse(text); } catch (err) { data = null; }
  }
  if (!res.ok) {
    throw new AppError((data && data.error && data.error.code) || ('http_' + res.status), humanMessage(data, res.status));
  }
  if (!text) return {};
  if (data === null) throw new AppError('bad_response', ERROR_CN.bad_response);
  return data;
}
const jobPath = () => encodeURIComponent(state.jobId || '');

function toast(message, actions, ms) {
  ui.toastMsg.textContent = String(message == null ? '' : message);
  clear(ui.toastActions);
  (actions || []).forEach((action) => {
    const btn = mk('button', null, action.label || '好');
    btn.type = 'button';
    btn.addEventListener('click', () => {
      try { if (typeof action.fn === 'function') action.fn(); } finally { hideToast(); }
    });
    ui.toastActions.appendChild(btn);
  });
  ui.toast.classList.add('show');
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(hideToast, ms || ((actions && actions.length) ? 6000 : 3800));
}
function hideToast() {
  ui.toast.classList.remove('show');
  clearTimeout(state.toastTimer);
}

/* ─────────────── 屏幕 / 步骤 / 页脚 ─────────────── */
function setScreen(name) {
  state.screen = name;
  $$('.screen').forEach((node) => node.classList.toggle('active', node.id === 'screen-' + name));
  if (name !== 'console') {
    state.selectMode = false;
    ui.stageOrig && ui.stageOrig.classList.remove('alt-select');
    ui.btnSelect && ui.btnSelect.classList.remove('on');
  }
  const step = name === 'drop' ? 1 : (name === 'scan' ? 2 : (state.exported ? 4 : 3));
  setStep(step);
}
function setStep(n) {
  $$('.step').forEach((node) => {
    const k = +node.dataset.step;
    node.classList.toggle('done', k < n);
    node.classList.toggle('active', k === n);
  });
}
function renderNet() {
  const h = state.health;
  const net = h ? h.net : null;
  const blocked = net === 'blocked' || net === true || !!(net && net.blocked === true);
  ui.netBadge.classList.toggle('off', !blocked);
  ui.netText.textContent = blocked ? '本机处理 · 不联网' : (h ? '本机处理 · 未确认断网' : '本机服务未连接');
  ui.footDot.className = 'netdot ' + (blocked ? 'ok' : (h ? 'warn' : 'off'));
  ui.footNet.textContent = blocked ? '本机处理 · 不联网' : (h ? '本机处理 · 未确认断网' : '本机服务未连接');
  ui.footMeta.textContent = h ? '数据隐站 · v' + (h.version || '1.0.0') : '数据隐站 · 本机服务';
}
async function loadHealth() {
  try { state.health = await api('/api/health'); } catch (err) { state.health = null; }
  renderNet();
}
function makeDocNo() {
  const d = new Date();
  const ymd = '' + d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
  return 'No.' + ymd + '-' + String(Math.floor(Math.random() * 9000) + 1000);
}

/* ─────────────── 证件脱敏档位（投放台 + 对照台头部） ───────────────
   用户已批准方案 A：默认「关键字段」（只遮头像/姓名/身份证号/住址），整卡涂黑可选。
   选择结果在创建任务时作为 id_card_mode 发给后端；任务创建后再改只提示重新投放生效。 */
function buildIdModeBlock(compact) {
  const wrap = mk('div', 'idmode' + (compact ? ' compact' : ''));
  wrap.appendChild(mk('span', 'idmode-label', compact ? '证件脱敏' : '证件脱敏（身份证）'));
  const seg = mk('div', 'idmode-seg');
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', '证件脱敏档位');
  ID_MODE_OPTIONS.forEach((opt) => {
    const btn = mk('button', null, compact ? opt.short : opt.label);
    btn.type = 'button';
    btn.dataset.idmode = opt.value;
    btn.setAttribute('aria-pressed', 'false');
    btn.title = ID_MODE_CN[opt.value] + '：' + ID_MODE_DESC[opt.value];
    btn.addEventListener('click', () => setIdCardMode(opt.value));
    seg.appendChild(btn);
  });
  wrap.appendChild(seg);
  if (compact) {
    ui.idModeJobHint = mk('span', 'idmode-job', '');
    wrap.appendChild(ui.idModeJobHint);
  } else {
    wrap.appendChild(mk('div', 'idmode-hint',
      '关键字段：遮头像 / 姓名 / 身份证号 / 住址 + 反面签发机关 / 有效期；整卡：整卡覆盖（公开 / 不确定场景推荐）'));
  }
  return wrap;
}
function mountIdCardMode() {
  const dropWrap = ui.dropzone && ui.dropzone.closest ? ui.dropzone.closest('.drop-wrap') : null;
  if (dropWrap && !dropWrap.querySelector('.idmode')) {
    const actions = dropWrap.querySelector('.drop-actions');
    const block = buildIdModeBlock(false);
    if (actions && actions.parentNode === dropWrap) actions.insertAdjacentElement('afterend', block);
    else dropWrap.appendChild(block);
    ui.idModeDrop = block;
  }
  const strict = ui.strictSel && ui.strictSel.closest ? ui.strictSel.closest('.strict') : null;
  if (strict && strict.parentNode && !strict.parentNode.querySelector('.idmode.compact')) {
    const block = buildIdModeBlock(true);
    strict.parentNode.insertBefore(block, strict);
    ui.idModeHead = block;
  }
  syncIdCardModeUI();
}
function syncIdCardModeUI() {
  const mode = normalizeIdCardMode(state.idCardMode);
  $$('.idmode [data-idmode]').forEach((btn) => {
    const on = btn.dataset.idmode === mode;
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', String(on));
  });
  if (ui.idModeJobHint) {
    const jobMode = (state.jobId && state.job) ? normalizeIdCardMode(state.job.id_card_mode) : null;
    ui.idModeJobHint.textContent = jobMode ? '本次任务：' + ID_MODE_CN[jobMode] : '';
    ui.idModeJobHint.title = jobMode ? '当前任务使用的档位；改动选择只影响下一次投放' : '';
  }
}
function setIdCardMode(value) {
  const next = normalizeIdCardMode(value);
  if (next === state.idCardMode) return;
  state.idCardMode = next;
  syncIdCardModeUI();
  if (state.jobId) {
    toast('证件脱敏改为「' + ID_MODE_CN[next] + '」（' + ID_MODE_DESC[next] +
      '）。已识别的任务不会重跑，重新投放文件后生效。', null, 7000);
  }
}

/* ─────────────── ① 投放台 ─────────────── */
function bindDropzone() {
  const openPicker = () => ui.fileInput.click();
  ui.dropzone.addEventListener('click', openPicker);
  ui.dropzone.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openPicker(); }
  });
  ui.btnChoose.addEventListener('click', openPicker);
  ui.fileInput.addEventListener('change', () => {
    const file = ui.fileInput.files && ui.fileInput.files[0];
    ui.fileInput.value = '';
    if (file) startFlow(file);
  });
  ['dragenter', 'dragover'].forEach((type) => {
    ui.dropzone.addEventListener(type, (ev) => {
      ev.preventDefault(); ev.stopPropagation(); ui.dropzone.classList.add('hot');
    });
  });
  ['dragleave', 'drop'].forEach((type) => {
    ui.dropzone.addEventListener(type, (ev) => {
      ev.preventDefault(); ev.stopPropagation(); ui.dropzone.classList.remove('hot');
    });
  });
  ui.dropzone.addEventListener('drop', (ev) => {
    const file = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (file) startFlow(file);
  });
  window.addEventListener('dragover', (ev) => ev.preventDefault());
  window.addEventListener('drop', (ev) => ev.preventDefault());
}
function resetJobState() {
  stopPolling();
  state.job = null;
  state.jobId = null;
  state.counts = { total: 0, done: 0, pending: 0 };
  state.pages = 1;
  state.page = 0;
  state.entities = [];
  state.previews = { orig: new Map(), out: new Map() };
  state.entityPage = new Map();
  state.pageLoading = null;
  state.exported = false;
  state.finishing = false;
  state.lastStageText = '';
  state.lastScanPage = 0;
  state.totalLogged = false;
  state.typeLogged = false;
  state.lastTypesKey = '';
  state.loggedTypes = new Set();
  state.typeSummaryLogged = false;
  state.markDraft = null;
  state.markMenu = null;
  state.pollFailures = 0;
}
async function startFlow(file) {
  if (!file) return;
  closeExportModal(true);
  state.uploadId = null;
  state.fileName = file.name || '未命名文件';
  state.mode = 'sur';           // 每份新文件都从「换名」开始，涂黑要人主动选
  syncModeButtons();
  resetJobState();
  beginScan(state.fileName);
  setScreen('scan');
  logScan('本机处理，文件不出这台机器。');
  logScan('证件脱敏：' + ID_MODE_CN[normalizeIdCardMode(state.idCardMode)] +
    '（' + ID_MODE_DESC[normalizeIdCardMode(state.idCardMode)] + '）');
  try {
    setScanStage('正在接收文件…');
    const headers = { 'X-Filename': encodeURIComponent(file.name || ''), 'Content-Type': file.type || 'application/octet-stream' };
    const up = await api('/api/upload', { method: 'POST', headers, body: file });
    if (!up || !up.upload_id) throw new AppError('bad_response', '本机没有收下这个文件，请再拖一次。');
    state.uploadId = up.upload_id;
    if (up.name) state.fileName = up.name;
    logScan('已收下 ' + (fmtSize(up.size) || '文件') + ' · 开始识别');
    await createJob();
  } catch (err) {
    scanFailed(err);
  }
}
async function createJob() {
  if (!state.uploadId) throw new AppError('missing_file', ERROR_CN.missing_file);
  setScanStage('正在建立本机任务…');
  const idCardMode = normalizeIdCardMode(state.idCardMode);
  const res = await api('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      upload_id: state.uploadId,
      strictness: state.strictness,
      id_card_mode: idCardMode,
    }),
  });
  if (!res || !res.job_id) throw new AppError('bad_response', '本机没有给出任务编号，请重新投一次。');
  state.jobId = String(res.job_id);
  if (res.id_card_mode) state.idCardMode = normalizeIdCardMode(res.id_card_mode);
  syncIdCardModeUI();
  startPolling();
}
function scanFailed(err) {
  stopPolling();
  clearTimeout(state.scanTimer);
  state.scanTyping = false;
  state.scanQueue = [];
  const human = (err && (err.human || (err.code && ERROR_CN[err.code]))) || '这份文件没能处理，原件没有被改动。';
  if (!(err && err.human)) console.warn('处理失败：', err);
  logScan('✕ ' + human, { hl: true });
  toast(human);
  setTimeout(() => { if (state.screen === 'scan') setScreen('drop'); }, 1800);
}

/* ─────────────── ② 识别中：轮询 + 打字机 ─────────────── */
function beginScan(name) {
  clear(ui.scanLines);
  clearTimeout(state.scanTimer);
  state.scanQueue = [];
  state.scanTyping = false;
  ui.scanLines.scrollTop = 0;
  setScanProgress(0);
  setScanStage('正在接收文件…');
  ui.scanCount.textContent = '—';
  logScan('正在翻阅：' + name);
}
function setScanProgress(v) {
  ui.scanProgress.style.width = (clamp(v, 0, 1) * 100).toFixed(1) + '%';
}
function setScanStage(text) { ui.scanStage.textContent = text; }
function logScan(text, opts = {}) {
  state.scanQueue.push({ text: String(text), hl: !!opts.hl });
  pumpScanLog();
}
function pumpScanLog() {
  if (state.scanTyping) return;
  const item = state.scanQueue.shift();
  if (!item) return;
  state.scanTyping = true;
  const line = mk('div', 'ln' + (item.hl ? ' hl' : ''));
  const caret = mk('span', 'caret');
  line.appendChild(caret);
  ui.scanLines.appendChild(line);
  ui.scanLines.scrollTop = ui.scanLines.scrollHeight;
  const text = item.text;
  if (state.motionReduced) {
    line.textContent = text;
    state.scanTyping = false;
    pumpScanLog();
    return;
  }
  let i = 0;
  const step = () => {
    if (i < text.length) {
      caret.insertAdjacentText('beforebegin', text[i++]);
      ui.scanLines.scrollTop = ui.scanLines.scrollHeight;
      state.scanTimer = setTimeout(step, 12 + Math.random() * 14);
    } else {
      caret.remove();
      state.scanTyping = false;
      state.scanTimer = setTimeout(pumpScanLog, 70);
    }
  };
  step();
}
function waitTypedDrain() {
  return new Promise((resolve) => {
    const started = Date.now();
    const tick = () => {
      if ((!state.scanTyping && state.scanQueue.length === 0) || Date.now() - started > 2600) resolve();
      else setTimeout(tick, 50);
    };
    tick();
  });
}
function stageText(stage) {
  const key = String(stage || '').toLowerCase();
  if (!key) return '正在处理…';
  if (STAGE_CN[key]) return STAGE_CN[key];
  if (/[\u4e00-\u9fa5]/.test(key)) return String(stage);
  return '正在处理…';
}
function progress01(job) {
  if (!job) return null;
  const raw = Number(job.progress);
  if (!Number.isFinite(raw)) {
    const c = job.counts || {};
    return num(c.total) > 0 ? num(c.done) / num(c.total) : null;
  }
  const p = raw > 1 ? raw / 100 : raw;
  return clamp(p, 0, 1);
}
function extractTypeCounts(job) {
  const c = job && job.counts;
  const src = (job && (job.found || job.types || job.by_type || job.entity_counts)) ||
    (c && (c.by_type || c.types)) || null;
  const out = [];
  if (!src) return out;
  if (Array.isArray(src)) {
    src.forEach((item) => {
      if (!item || typeof item !== 'object') return;
      const type = item.type || item.etype || item.label;
      const count = num(item.count != null ? item.count : item.n);
      if (type && count > 0) out.push({ type: String(type), count });
    });
  } else if (typeof src === 'object') {
    Object.keys(src).forEach((type) => {
      const count = num(src[type]);
      if (count > 0) out.push({ type, count });
    });
  }
  return out;
}
function startPolling() {
  stopPolling();
  pollJob();
  state.pollTimer = setInterval(pollJob, 600);
}
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}
async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api('/api/jobs/' + jobPath());
    state.job = job || state.job;
    state.pollFailures = 0;
    updateScan(state.job);
    if (state.job && state.job.error) {
      stopPolling();
      const human = jobErrorMessage(state.job.error);
      logScan('✕ ' + human, { hl: true });
      toast(human);
      setTimeout(() => { if (state.screen === 'scan') setScreen('drop'); }, 2000);
      return;
    }
    if (isJobDone(state.job)) { stopPolling(); await finishScan(state.job); }
  } catch (err) {
    state.pollFailures += 1;
    if (state.pollFailures >= 5) {
      stopPolling();
      const human = (err && err.human) || ERROR_CN.unreachable;
      logScan('✕ ' + human, { hl: true });
      toast(human);
      setTimeout(() => { if (state.screen === 'scan') setScreen('drop'); }, 1800);
    }
  }
}
function isJobDone(job) {
  if (!job) return false;
  const stage = String(job.stage || '').toLowerCase();
  if (DONE_STAGES.includes(stage)) return true;
  const c = job.counts;
  const p = progress01(job);
  return !!(c && num(c.total) > 0 && p != null && p >= 1);
}
function updateScan(job) {
  if (!job) return;
  const p = progress01(job);
  if (p != null) setScanProgress(p);
  const stage = stageText(job.stage);
  // 后端会给出「正在识别第 3/17 页」这类细粒度进度，优先显示它。
  // 扫描件一页要好几秒，没有这句话用户会以为卡死了。
  const note = job && job.note ? String(job.note) : '';
  const shown = note || stage;
  if (shown !== state.lastStageText) {
    state.lastStageText = shown;
    setScanStage(shown);
    if (!/正在接收|排队/.test(shown)) logScan('▸ ' + shown);
  }
  const pageNo = Math.floor(num(job.page));
  if (pageNo > 0 && pageNo !== state.lastScanPage) {
    state.lastScanPage = pageNo;
    logScan('▸ 翻到第 ' + String(pageNo).padStart(2, '0') + ' 页');
  }
  const c = job.counts || {};
  const total = num(c.total), done = num(c.done), pending = num(c.pending);
  if (total > 0) {
    ui.scanCount.textContent = '已处理 ' + done + ' / ' + total + ' 处' + (pending > 0 ? ' · 待确认 ' + pending : '');
    if (!state.totalLogged) { state.totalLogged = true; logScan('▸ 共 ' + total + ' 处，正在逐处处理'); }
  }
  const types = extractTypeCounts(job);
  if (types.length) {
    const key = JSON.stringify(types);
    if (key !== state.lastTypesKey) {
      state.lastTypesKey = key;
      types.forEach((t) => {
        const tag = t.type + ':' + t.count;
        if (state.loggedTypes.has(tag)) return;
        state.loggedTypes.add(tag);
        state.typeLogged = true;
        logScan('  · 识别到 ' + (TYPE_CN[t.type] || t.type) + ' × ' + t.count, { hl: true });
      });
    }
  }
}
async function finishScan(job) {
  if (state.finishing) return;
  state.finishing = true;
  clearTimeout(state.scanTimer);
  setScanProgress(1);
  setScanStage('清点完成');
  try {
    if (!state.entities.length) {
      const res = await api('/api/jobs/' + jobPath() + '/entities');
      state.entities = res && Array.isArray(res.entities) ? res.entities : [];
    }
    logTypeSummary();
    logScan('▸ 出口自检就绪 · 可以对照了', { hl: true });
  } catch (err) {
    /* 清单拿不到不阻塞进入对照台 */
  }
  await waitTypedDrain();
  state.finishing = false;
  if (state.screen !== 'scan') return;   // 期间用户已取消或换文件
  await enterConsole();
}
function logTypeSummary() {
  const list = state.entities.filter((e) => e && e.status !== 'rejected');
  if (!list.length) return;
  if (!state.typeLogged) {
    const groups = new Map();
    list.forEach((ent) => {
      const key = ent.type || 'OTHER';
      groups.set(key, (groups.get(key) || 0) + 1);
    });
    const rows = Array.from(groups.entries()).sort((a, b) => typeRank(a[0]) - typeRank(b[0]));
    logScan('▸ 识别到 ' + rows.map(([type, n]) => (TYPE_CN[type] || '其他敏感') + ' × ' + n).join(' · '), { hl: true });
    state.typeLogged = true;
  }
  if (!state.totalLogged) {
    state.totalLogged = true;
    logScan('▸ 共 ' + list.length + ' 处，按「' + (STRICT_CN[state.strictness] || '标准') + '」准备对照');
  }
  const pending = list.filter((e) => e.status === 'pending').length;
  if (pending > 0) logScan('▸ ' + pending + ' 处没把握，等你确认', { hl: true });
  state.typeSummaryLogged = true;
}
function typeRank(type) {
  const i = TYPE_ORDER.indexOf(type);
  return i < 0 ? TYPE_ORDER.length : i;
}

/* ─────────────── ③ 对照台：加载与公共信息 ─────────────── */
function totalPages(job) {
  if (!job) return 1;
  if (Array.isArray(job.pages)) return Math.max(1, job.pages.length);
  const n = Math.floor(num(job.pages));
  return n > 0 ? n : 1;
}
function readCounts(job, entities) {
  const c = job && job.counts;
  if (c && Number.isFinite(Number(c.total))) {
    // 撤销（reject）的条目不再算「要隐去处」，与右侧清单保持一致
    const rejected = Math.max(0, num(c.rejected));
    return {
      total: Math.max(0, num(c.total) - rejected),
      done: Math.max(0, num(c.done) - rejected),
      pending: Math.max(0, num(c.pending)),
    };
  }
  const list = (entities || []).filter((e) => e && e.status !== 'rejected');
  const pending = list.filter((e) => e.status === 'pending').length;
  return { total: list.length, done: list.length - pending, pending };
}
function activeEntityCount() {
  return state.entities.filter((e) => e && e.status !== 'rejected').length;
}
function updateConsoleHead() {
  ui.cntAll.textContent = String(state.counts.total);
  ui.cntDone.textContent = String(state.counts.done);
  ui.cntPend.textContent = String(state.counts.pending);
  ui.reviewCount.textContent = String(state.counts.pending);
  ui.btnAcceptAll.disabled = state.counts.pending <= 0;
  ui.btnReview.disabled = state.counts.pending <= 0;
  ui.railCount.textContent = String(activeEntityCount());
  ui.docName.textContent = state.fileName || (state.job && state.job.name) || '未命名文件';
  syncWholePageButton();
  syncIdCardModeUI();
}
function updatePager() {
  ui.pageLabel.textContent = (state.page + 1) + ' / ' + state.pages;
  ui.btnPrevPage.disabled = state.page <= 0;
  ui.btnNextPage.disabled = state.page >= state.pages - 1;
}
function showFramesLoading(message) {
  [ui.frameOrig, ui.frameOut].forEach((frame) => {
    clear(frame);
    frame.appendChild(mk('div', 'loading', message || '正在准备这一页…'));
  });
}
function rememberPreview(preview, page) {
  if (!preview) return;
  (preview.entities || []).forEach((ent) => {
    if (ent && ent.id && !state.entityPage.has(ent.id)) state.entityPage.set(ent.id, page);
  });
}
async function enterConsole() {
  if (!state.jobId) return;
  stopPolling();
  setScreen('console');
  state.page = 0;
  state.pageLoading = null;
  state.previews = { orig: new Map(), out: new Map() };
  state.entityPage = new Map();
  ui.docName.textContent = state.fileName || '未命名文件';
  document.title = (state.fileName || '对照台') + ' · 数据隐站';
  showFramesLoading('正在装订对照台…');
  try {
    const jid = jobPath();
    const [job, ents] = await Promise.all([
      api('/api/jobs/' + jid),
      state.entities.length ? Promise.resolve({ entities: state.entities }) : api('/api/jobs/' + jid + '/entities'),
    ]);
    if (job) state.job = job;
    if (ents && Array.isArray(ents.entities)) state.entities = ents.entities;
    state.pages = Math.max(1, totalPages(state.job));
    state.counts = readCounts(state.job, state.entities);
    updateConsoleHead();
    updatePager();
    buildRail();
    syncFootHint();
    await loadPage(0, { keepScroll: false });
    const warnings = state.job && state.job.warnings;
    if (Array.isArray(warnings) && warnings.length) {
      toast('这份文件有 ' + warnings.length + ' 条提示，出口自检时会再查一遍。');
    }
  } catch (err) {
    toast((err && err.human) || '对照台没能准备好，请重新投放文件。');
    setScreen('drop');
    resetJobState();
  }
}
async function loadPage(page, opts = {}) {
  if (!state.jobId) return;
  const target = clamp(Math.floor(num(page)), 0, Math.max(0, state.pages - 1));
  state.page = target;
  updatePager();
  if (state.previews.orig.has(target) && state.previews.out.has(target)) {
    renderAll(opts);
    return;
  }
  if (state.pageLoading === target) return;
  state.pageLoading = target;
  showFramesLoading('正在准备这一页…');
  const jid = jobPath();
  try {
    const [orig, out] = await Promise.all([
      state.previews.orig.get(target) || api('/api/jobs/' + jid + '/preview/' + target + '?mode=orig'),
      state.previews.out.get(target) || api('/api/jobs/' + jid + '/preview/' + target + '?mode=out'),
    ]);
    if (state.page !== target) return;
    state.previews.orig.set(target, orig);
    state.previews.out.set(target, out);
    rememberPreview(orig, target);
    rememberPreview(out, target);
    state.pages = Math.max(1, num(orig && orig.total_pages) || state.pages);
    updatePager();
    renderAll(opts);
  } catch (err) {
    if (state.page === target) {
      showFramesLoading('这一页没能准备好。');
      toast((err && err.human) || '这一页没能准备好，翻到别页再回来试试。');
    }
  } finally {
    if (state.pageLoading === target) state.pageLoading = null;
  }
}
function renderAll(opts = {}) {
  const orig = state.previews.orig.get(state.page);
  const out = state.previews.out.get(state.page);
  const keep = !!opts.keepScroll;
  const ratio = keep ? scrollRatio(ui.stageOrig) : 0;
  state.markIdx = 0;
  renderPane('orig', orig);
  renderPane('out', out);
  bindPeek(ui.frameOut);
  if (keep) requestAnimationFrame(() => { setScrollRatio(ui.stageOrig, ratio); setScrollRatio(ui.stageOut, ratio); });
  updatePager();
  updateConsoleHead();
}
function renderPane(side, preview) {
  const frame = side === 'orig' ? ui.frameOrig : ui.frameOut;
  clear(frame);
  if (!preview) {
    frame.appendChild(mk('div', 'loading', '这一页还没准备好。'));
    return;
  }
  const kind = preview.kind || ((Array.isArray(preview.blocks) && preview.blocks.length) ? 'flow' : 'raster');
  if (kind === 'raster') renderRaster(frame, preview, side);
  else renderFlow(frame, preview, side);
}

/* ─────────────── ③ 对照台：raster 渲染 ─────────────── */
function entityBoxes(ent) {
  if (!ent) return [];
  if (Array.isArray(ent.boxes) && ent.boxes.length) return ent.boxes;
  if (Array.isArray(ent.box) && ent.box.length >= 4) return [ent.box];
  return [];
}
function labelOf(ent) { return (ent && (ent.label || TYPE_CN[ent.type])) || '敏感内容'; }
function surrogateOf(ent) {
  if (!ent) return '＊';
  if (ent.surrogate) return String(ent.surrogate);
  const value = String(ent.value || '');
  if (!value) return '＊';
  if (value.length <= 2) return '＊'.repeat(value.length);
  return value[0] + '＊'.repeat(value.length - 1);
}
function renderRaster(frame, preview, side) {
  const W = Math.max(1, num(preview.width) || 595);
  const H = Math.max(1, num(preview.height) || 842);
  const sheet = mk('div', 'sheet');
  sheet.dataset.side = side;
  sheet.style.aspectRatio = W + ' / ' + H;
  const img = document.createElement('img');
  img.alt = side === 'orig' ? '原件页面' : '可外发版页面';
  img.draggable = false;
  img.src = '/api/jobs/' + jobPath() + '/render/' + Math.floor(num(preview.page)) + '?mode=orig';
  sheet.appendChild(img);
  const overlay = mk('div', 'overlay');
  sheet.appendChild(overlay);
  frame.appendChild(sheet);

  const entities = (Array.isArray(preview.entities) ? preview.entities : []).filter((e) => e && e.status !== 'rejected');
  let blackIdx = 0;
  entities.forEach((ent) => {
    entityBoxes(ent).forEach((rawBox) => {
      if (!Array.isArray(rawBox) || rawBox.length < 4) return;
      const box = rawBox.map(Number);
      if (!box.slice(0, 4).every(Number.isFinite)) return;
      const left = clamp(Math.min(box[0], box[2]), 0, W);
      const top = clamp(Math.min(box[1], box[3]), 0, H);
      const right = clamp(Math.max(box[0], box[2]), 0, W);
      const bottom = clamp(Math.max(box[1], box[3]), 0, H);
      if (right - left <= 0 || bottom - top <= 0) return;
      const node = mk('div');
      node.dataset.eid = ent.id || '';
      node.style.left = pct(left / W);
      node.style.top = pct(top / H);
      node.style.width = pct((right - left) / W);
      node.style.height = pct((bottom - top) / H);
      if (side === 'orig') {
        node.className = 'box orig';
        node.title = labelOf(ent) + ' · 原件只在本机显示';
      } else if (state.mode === 'black') {
        node.className = 'box black';
        node.style.setProperty('--i', String(blackIdx++));
        node.dataset.raw = String(ent.value || '');
        node.title = '按住 300 毫秒可透视原值';
      } else {
        node.className = 'box sur';
        node.appendChild(mk('span', 'sur-label', surrogateOf(ent)));
        node.title = labelOf(ent) + ' · 换名后';
      }
      overlay.appendChild(node);
    });
  });
  const size = () => sizeSurLabels(overlay);
  img.addEventListener('load', size);
  if (img.complete) requestAnimationFrame(size);
  if (side === 'orig') bindSelection(sheet, overlay, preview, 'raster');
}
function sizeSurLabels(root) {
  $$('.box.sur', root).forEach((box) => {
    const label = box.firstElementChild;
    if (!label) return;
    const h = box.clientHeight;
    if (h > 0) label.style.fontSize = Math.max(8, Math.min(15, Math.round(h * 0.46))) + 'px';
  });
}

/* ─────────────── ③ 对照台：flow 渲染 ─────────────── */
function normalizeSegs(segs) {
  segs.sort((a, b) => a.start - b.start || b.end - a.end);
  const out = [];
  let cursor = 0;
  segs.forEach((seg) => {
    if (seg.end <= cursor) return;
    if (seg.start < cursor) seg.start = cursor;
    if (seg.end > seg.start) { out.push(seg); cursor = seg.end; }
  });
  segs.length = 0;
  segs.push.apply(segs, out);
}
function collectFlowSpans(preview, blocks) {
  const byBlock = new Map();
  const blockById = new Map((blocks || []).map((b) => [b.id, b]));
  (preview.entities || []).forEach((ent) => {
    if (!ent || ent.status === 'rejected') return;
    (ent.spans || []).forEach((sp) => {
      if (!sp || typeof sp.block_id !== 'string') return;
      const block = blockById.get(sp.block_id);
      if (!block) return;
      const textLen = String(block.text || '').length;
      const start = Math.max(0, Math.floor(num(sp.start)));
      const end = Math.min(textLen, Math.floor(num(sp.end)));
      if (end <= start) return;
      if (!byBlock.has(sp.block_id)) byBlock.set(sp.block_id, []);
      byBlock.get(sp.block_id).push({ start, end, ent });
    });
  });
  byBlock.forEach((segs) => normalizeSegs(segs));
  return byBlock;
}
function flowBlocksForSide(preview, side) {
  const blocks = Array.isArray(preview.blocks) ? preview.blocks : [];
  const fallback = () => {
    const orig = state.previews.orig.get(Math.floor(num(preview.page)));
    return (orig && Array.isArray(orig.blocks) && orig.blocks.length) ? orig.blocks : blocks;
  };
  if (side !== 'out') return blocks;
  if (!blocks.length) return fallback();
  const byId = new Map(blocks.map((b) => [b.id, b]));
  const entities = (preview.entities || []).filter((e) => e && e.status !== 'rejected');
  for (const ent of entities) {
    for (const sp of ent.spans || []) {
      if (!sp || typeof sp.block_id !== 'string' || !(num(sp.end) > num(sp.start))) continue;
      const block = byId.get(sp.block_id);
      if (!block) return fallback();
      const slice = String(block.text || '').slice(Math.floor(num(sp.start)), Math.floor(num(sp.end)));
      if (slice !== String(ent.value || '')) return fallback();
    }
  }
  return blocks;
}
function renderFlow(frame, preview, side) {
  // 后端若在成品预览里漏发了实体/跨度，退回原件预览的实体来渲染（保证两边对得上）
  let source = preview;
  if (side === 'out' && !(preview.entities || []).length) {
    const orig = state.previews.orig.get(Math.floor(num(preview.page)));
    if (orig && Array.isArray(orig.entities) && orig.entities.length) source = orig;
  }
  const paper = mk('div', 'paper');
  const meta = mk('div', 'paper-meta');
  meta.appendChild(mk('span', null, side === 'orig' ? '隐站 · 输入副本' : '隐站 · 输出副本'));
  meta.appendChild(mk('span', side === 'orig' ? 'cls' : '', side === 'orig' ? '内部' : state.docNo));
  paper.appendChild(meta);
  if (side === 'out') paper.appendChild(mk('div', 'stamp-out', '已 隐 去 · 可 外 发'));
  const body = mk('div', 'doc-body');
  const blocks = flowBlocksForSide(preview, side);
  const spanMap = collectFlowSpans(source, blocks);
  const allEnts = (source.entities || []).filter((e) => e && e.status !== 'rejected');
  state.markIdx = 0;
  blocks.forEach((block) => body.appendChild(renderFlowBlock(block, spanMap, side, allEnts)));
  paper.appendChild(body);
  const overlay = mk('div', 'overlay');
  paper.appendChild(overlay);
  frame.appendChild(paper);
  if (side === 'orig') bindSelection(paper, overlay, preview, 'flow');
}
function renderFlowBlock(block, spanMap, side, allEnts) {
  if (!block || typeof block !== 'object') return mk('p', 'blk', '');
  if (block.kind === 'table') return renderFlowTable(block, spanMap, side, allEnts);
  if (block.kind === 'image') {
    const box = mk('div', 'blk imgblk', '［图片内容］');
    if (block.bbox) box.title = '图片：' + block.bbox.map((v) => Math.round(num(v))).join(',');
    return box;
  }
  const p = mk('p', 'blk');
  appendMarkedText(p, String(block.text || ''), spanMap.get(block.id) || [], side);
  return p;
}
function appendMarkedText(parent, text, segs, side) {
  let cursor = 0;
  segs.forEach((seg) => {
    const start = Math.max(cursor, Math.min(seg.start, text.length));
    const end = Math.min(seg.end, text.length);
    if (end <= start) return;
    if (start > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, start)));
    parent.appendChild(markedSpan(text.slice(start, end), seg.ent, side));
    cursor = end;
  });
  if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
}
function markedSpan(raw, ent, side) {
  const span = mk('span');
  span.dataset.eid = ent.id || '';
  if (side === 'orig') {
    span.className = 'mark';
    span.textContent = raw;
  } else if (state.mode === 'black') {
    span.className = 'redacted black';
    const visible = raw || String(ent.value || '');
    span.textContent = '█'.repeat(Math.max(1, visible.length));
    span.dataset.raw = raw || String(ent.value || '');
    span.dataset.blackText = span.textContent;
    span.style.setProperty('--i', String(state.markIdx++));
    span.title = '按住 300 毫秒可透视原值';
  } else {
    span.className = 'redacted sur';
    span.textContent = surrogateOf(ent);
  }
  return span;
}
function renderFlowTable(block, spanMap, side, allEnts) {
  const cells = Array.isArray(block.cells) ? block.cells.slice() : [];
  if (!cells.length) {
    const p = mk('p', 'blk');
    p.textContent = String(block.text || '');
    return p;
  }
  let rows = 0, cols = 0;
  cells.forEach((c) => {
    rows = Math.max(rows, Math.floor(num(c.row)));
    cols = Math.max(cols, Math.floor(num(c.col)));
  });
  rows += 1; cols += 1;
  const grid = Array.from({ length: rows }, () => Array(cols).fill(null));
  cells.forEach((c) => {
    const r = Math.floor(num(c.row)), col = Math.floor(num(c.col));
    if (r >= 0 && r < rows && col >= 0 && col < cols) grid[r][col] = c;
  });
  const blockEnts = (spanMap.get(block.id) || []).map((s) => s.ent);
  // 表格单元格在 UDM 里是独立的 text 块，实体 span 不一定挂在表格块上。
  // 这里退回「按值匹配」：只要单元格文字里含某个实体的值，就按该实体处理。
  const entsForCells = blockEnts.length ? blockEnts : (Array.isArray(allEnts) ? allEnts : []);
  const table = mk('table', 'tbl');
  for (let r = 0; r < rows; r++) {
    const tr = mk('tr');
    for (let c = 0; c < cols; c++) {
      const td = mk('td');
      const cell = grid[r][c];
      const text = cell ? String(cell.text || '') : '';
      appendMarkedText(td, text, valueSegs(text, entsForCells), side);
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
  return table;
}
function valueSegs(text, entities) {
  const found = [];
  const seen = new Set();
  entities.forEach((ent) => {
    if (!ent || seen.has(ent.id)) return;
    seen.add(ent.id);
    const value = String(ent.value || '');
    if (!value) return;
    let at = text.indexOf(value);
    while (at >= 0) {
      found.push({ start: at, end: at + value.length, ent });
      at = text.indexOf(value, at + value.length);
    }
  });
  found.sort((a, b) => a.start - b.start);
  const out = [];
  let cursor = 0;
  found.forEach((seg) => {
    if (seg.start < cursor) return;
    out.push(seg);
    cursor = seg.end;
  });
  return out;
}

/* ─────────────── ③ 交互：透视 / 同步滚动 / 分隔线 ─────────────── */
function bindPeek(root) {
  $$('.redacted.black', root).forEach((node) => {
    if (node.dataset.peekBound) return;
    node.dataset.peekBound = '1';
    let timer = null;
    const show = () => {
      node.classList.add('peek');
      if (node.dataset.blackText != null) node.textContent = node.dataset.raw || '';
    };
    const hide = () => {
      clearTimeout(timer); timer = null;
      node.classList.remove('peek');
      if (node.dataset.blackText != null) node.textContent = node.dataset.blackText;
    };
    node.addEventListener('pointerdown', (ev) => { ev.preventDefault(); timer = setTimeout(show, 300); });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach((type) => node.addEventListener(type, hide));
  });
  $$('.box.black', root).forEach((node) => {
    if (node.dataset.peekBound) return;
    node.dataset.peekBound = '1';
    let timer = null;
    node.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      timer = setTimeout(() => node.classList.add('peek'), 300);
    });
    const hide = () => { clearTimeout(timer); timer = null; node.classList.remove('peek'); };
    ['pointerup', 'pointerleave', 'pointercancel'].forEach((type) => node.addEventListener(type, hide));
  });
  $$('.mark, .redacted.sur', root).forEach((node) => {
    if (node.dataset.locateBound) return;
    node.dataset.locateBound = '1';
    node.addEventListener('click', () => { if (node.dataset.eid) flashRail(node.dataset.eid); });
  });
}
function highlightRail(eid) {
  const row = $$('#railList .row').find((r) => r.dataset.eid === eid);
  if (!row) return;
  row.scrollIntoView({ block: 'nearest', behavior: state.motionReduced ? 'auto' : 'smooth' });
  row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
  setTimeout(() => row.classList.remove('flash'), 2400);
}
const flashRail = highlightRail;
function scrollRatio(stage) {
  const max = stage.scrollHeight - stage.clientHeight;
  return max > 0 ? stage.scrollTop / max : 0;
}
function setScrollRatio(stage, ratio) {
  const max = stage.scrollHeight - stage.clientHeight;
  if (max > 0) stage.scrollTop = clamp(ratio, 0, 1) * max;
}
function bindSyncScroll() {
  let lock = false;
  const link = (from, to) => from.addEventListener('scroll', () => {
    if (lock) return;
    lock = true;
    setScrollRatio(to, scrollRatio(from));
    requestAnimationFrame(() => { lock = false; });
  });
  link(ui.stageOrig, ui.stageOut);
  link(ui.stageOut, ui.stageOrig);
}
function bindDivider() {
  ui.divider.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    const body = ui.consoleBody;
    const move = (e) => {
      const rect = body.getBoundingClientRect();
      const rail = ui.rail && window.getComputedStyle(ui.rail).display !== 'none' ? ui.rail.offsetWidth + 14 : 0;
      const usable = Math.max(1, rect.width - rail - 36);
      let ratio = (e.clientX - rect.left - 18) / usable;
      ratio = clamp(ratio, 0.22, 0.78);
      ui.paneOrig.style.flexGrow = String(ratio);
      ui.paneOut.style.flexGrow = String(1 - ratio);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  });
}

/* ─────────────── ④ 框选补漏 ─────────────── */
function setSelectMode(on) {
  state.selectMode = !!on;
  ui.btnSelect.classList.toggle('on', state.selectMode);
  ui.btnSelect.setAttribute('aria-pressed', String(state.selectMode));
  ui.stageOrig.classList.toggle('alt-select', state.selectMode);
  if (state.selectMode) toast('在左边原件上拖一个框，标出遗漏的地方。按 Esc 退出。');
}
function closeMarkMenu() {
  $$('.menu, .marquee').forEach((node) => node.remove());
  state.markMenu = null;
}
function caretRangeAt(x, y) {
  if (document.caretRangeFromPoint) {
    try { return document.caretRangeFromPoint(x, y); } catch (err) { return null; }
  }
  if (document.caretPositionFromPoint) {
    try {
      const pos = document.caretPositionFromPoint(x, y);
      if (!pos) return null;
      const range = document.createRange();
      range.setStart(pos.offsetNode, pos.offset);
      range.collapse(true);
      return range;
    } catch (err) { return null; }
  }
  return null;
}
function normalizeRect(arr) {
  const out = [
    clamp(Math.min(arr[0], arr[2]), 0, 1), clamp(Math.min(arr[1], arr[3]), 0, 1),
    clamp(Math.max(arr[0], arr[2]), 0, 1), clamp(Math.max(arr[1], arr[3]), 0, 1),
  ];
  if (out[2] - out[0] < 0.002 || out[3] - out[1] < 0.002) return null;
  return out.map((v) => Math.round(v * 10000) / 10000);
}
function flowNorm(draft, ev, rect) {
  const base = draft.base;
  try {
    const r0 = caretRangeAt(draft.clientX0, draft.clientY0);
    const r1 = caretRangeAt(ev.clientX, ev.clientY);
    if (r0 && r1 && r0.startContainer && r1.startContainer) {
      const range = document.createRange();
      range.setStart(r0.startContainer, r0.startOffset);
      range.setEnd(r1.startContainer, r1.startOffset);
      const rects = Array.from(range.getClientRects());
      if (rects.length) {
        let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
        rects.forEach((r) => {
          left = Math.min(left, r.left); top = Math.min(top, r.top);
          right = Math.max(right, r.right); bottom = Math.max(bottom, r.bottom);
        });
        if (right - left > 2 && bottom - top > 2) {
          const norm = normalizeRect([
            (left - base.left) / base.width, (top - base.top) / base.height,
            (right - base.left) / base.width, (bottom - base.top) / base.height,
          ]);
          if (norm) return norm;
        }
      }
    }
  } catch (err) { /* 落回拖拽框 */ }
  return normalizeRect([
    rect.x / base.width, rect.y / base.height,
    (rect.x + rect.w) / base.width, (rect.y + rect.h) / base.height,
  ]);
}
function rasterNorm(preview, rect, base) {
  void preview;   // 归一化按显示框换算，后端再乘回页面坐标
  return normalizeRect([
    rect.x / base.width, rect.y / base.height,
    (rect.x + rect.w) / base.width, (rect.y + rect.h) / base.height,
  ]);
}
function bindSelection(host, overlay, preview, kind) {
  host.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    if (!state.selectMode && !ev.altKey) return;
    if (ev.target.closest && ev.target.closest('.menu')) return;
    ev.preventDefault();
    closeMarkMenu();
    const base = overlay.getBoundingClientRect();
    const x = clamp(ev.clientX - base.left, 0, base.width);
    const y = clamp(ev.clientY - base.top, 0, base.height);
    state.markDraft = {
      kind, preview, host, overlay, base,
      startX: x, startY: y, clientX0: ev.clientX, clientY0: ev.clientY,
    };
    const mq = mk('div', 'marquee');
    mq.style.left = x + 'px'; mq.style.top = y + 'px';
    mq.style.width = '0px'; mq.style.height = '0px';
    overlay.appendChild(mq);
    try { host.setPointerCapture(ev.pointerId); } catch (err) { /* 忽略 */ }
    document.body.classList.add('selecting');
  });
  host.addEventListener('pointermove', (ev) => {
    const draft = state.markDraft;
    if (!draft) return;
    const base = draft.base;
    const x = clamp(ev.clientX - base.left, 0, base.width);
    const y = clamp(ev.clientY - base.top, 0, base.height);
    const mq = $('.marquee', overlay);
    if (!mq) return;
    mq.style.left = Math.min(draft.startX, x) + 'px';
    mq.style.top = Math.min(draft.startY, y) + 'px';
    mq.style.width = Math.abs(x - draft.startX) + 'px';
    mq.style.height = Math.abs(y - draft.startY) + 'px';
  });
  const finish = (ev, cancelled) => {
    const draft = state.markDraft;
    if (!draft) return;
    state.markDraft = null;
    document.body.classList.remove('selecting');
    const mq = $('.marquee', overlay);
    const base = draft.base;
    const x = clamp(ev.clientX - base.left, 0, base.width);
    const y = clamp(ev.clientY - base.top, 0, base.height);
    const rect = {
      x: Math.min(draft.startX, x), y: Math.min(draft.startY, y),
      w: Math.abs(x - draft.startX), h: Math.abs(y - draft.startY),
    };
    if (mq) mq.remove();
    if (cancelled || rect.w < 8 || rect.h < 8) { closeMarkMenu(); return; }
    const norm = draft.kind === 'raster' ? rasterNorm(draft.preview, rect, base) : flowNorm(draft, ev, rect);
    if (!norm) { toast('这一块没能读出来，换个位置再拖一次。'); return; }
    openMarkMenu(draft, rect, norm);
  };
  host.addEventListener('pointerup', (ev) => finish(ev, false));
  host.addEventListener('pointercancel', (ev) => finish(ev, true));
}
function openMarkMenu(draft, rect, norm) {
  closeMarkMenu();
  const overlay = draft.overlay;
  const menu = mk('div', 'menu');
  menu.appendChild(mk('div', 'mh', '这一块是什么？'));
  MENU_ETYPES.forEach((item) => {
    const btn = mk('button', null, item.label);
    btn.type = 'button';
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      submitMark(draft, rect, norm, item.etype);
    });
    menu.appendChild(btn);
  });
  const maxLeft = Math.max(4, overlay.clientWidth - 140);
  const maxTop = Math.max(4, overlay.clientHeight - 180);
  menu.style.left = clamp(rect.x + rect.w + 8, 4, maxLeft) + 'px';
  menu.style.top = clamp(rect.y, 4, maxTop) + 'px';
  overlay.appendChild(menu);
  state.markMenu = menu;
}
async function submitMark(draft, rect, norm, etype) {
  closeMarkMenu();
  const bar = mk('div', 'manual-bar');
  bar.style.left = rect.x + 'px'; bar.style.top = rect.y + 'px';
  bar.style.width = rect.w + 'px'; bar.style.height = rect.h + 'px';
  draft.overlay.appendChild(bar);
  try {
    const res = await api('/api/jobs/' + jobPath() + '/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: state.page, rect: norm, etype, propagate: true }),
    });
    const propagated = num(res && res.propagated);
    const added = Math.max(1, num(res && res.added) || 1);
    if (propagated > 0) toast('同样内容还有 ' + propagated + ' 处，已一并处理。');
    else toast('已补记 ' + added + ' 处。');
    setSelectMode(false);
    await refreshLive();
  } catch (err) {
    bar.remove();
    toast((err && err.human) || '这一处没能补上，请再试一次。');
  }
}

/* ─────────────── ④b 整图涂黑（P0：照片/场景图漏检时的一次性兜底） ───────────────
   用途：人脸大图、场景照片等自动识别不到时，用户一键把「当前页」整页涂黑。
   接口：沿用现有 POST /api/jobs/{id}/mark，rect 传整页 [0,0,1,1]，etype=OTHER，
   label=整图脱敏，propagate=false；成功后复用 mark 后的 refreshLive 刷新实体/清单/预览。
   入口按钮由本文件动态插到「框选补漏」后面（不改 index.html）。 */
const WHOLE_PAGE_LABEL = '整图脱敏';
const FOOT_HINT_DEFAULT = '按住黑条 300 毫秒可透视原值 · 松手复原';

function imageLikeFormat() {
  const fmt = String((state.job && state.job.fmt) || '').toLowerCase();
  return fmt === 'image' || fmt === 'tiff';
}
function pageEntityCount() {
  const preview = state.previews.orig.get(state.page);
  const list = preview && Array.isArray(preview.entities) ? preview.entities : null;
  if (list) return list.filter((ent) => ent && ent.status !== 'rejected').length;
  return state.entities.filter((ent) => ent && ent.status !== 'rejected').length;
}
function syncFootHint() {
  const el = $('.foot-hint');
  if (!el) return;
  el.textContent = imageLikeFormat()
    ? '照片/扫描件：自动识别不到的内容，用「整图涂黑」一次盖掉 · 按住黑条 300 毫秒可透视'
    : FOOT_HINT_DEFAULT;
}
function mountWholePageButton() {
  if (ui.btnWholePage || !ui.btnSelect || !ui.btnSelect.parentNode) return;
  const btn = mk('button', 'btn danger', '⊘ 整图涂黑');
  btn.type = 'button';
  btn.id = 'btnWholePage';
  btn.title = '把当前页整页涂黑：照片、场景图里自动识别不到的隐私内容，用这一招一次性盖掉';
  btn.setAttribute('aria-label', '整图涂黑：把当前页整页涂黑，导出后不可还原');
  btn.addEventListener('click', onWholePageClick);
  ui.btnSelect.insertAdjacentElement('afterend', btn);
  ui.btnWholePage = btn;
}
/* 整图涂黑只对「能按页渲染」的格式有意义（图片/TIFF/PDF）；
   DOCX/XLSX/TXT 是文字排版（flow），整页矩形盖不到文字，禁用并说明，避免静默失效。 */
function wholePageAvailable() {
  if (!state.jobId) return false;
  const preview = state.previews.orig.get(state.page);
  if (!preview) return false;
  return (preview.kind || 'raster') === 'raster';
}
function syncWholePageButton() {
  const btn = ui.btnWholePage;
  if (!btn) return;
  const raster = wholePageAvailable();
  btn.disabled = !raster;
  btn.classList.toggle('unavailable', !raster);
  btn.title = raster
    ? '把当前页整页涂黑：照片、场景图里自动识别不到的隐私内容，用这一招一次性盖掉'
    : '整页涂黑只对图片、扫描件、PDF 有效：当前是文字排版格式（如 DOCX/XLSX），请用「框选补漏」标出要隐去的文字';
  btn.setAttribute('aria-label', raster
    ? '整图涂黑：把当前页整页涂黑，导出后不可还原'
    : '整图涂黑不可用：当前格式不是图片、扫描件或 PDF');
}
function wholePageConfirmText() {
  const parts = ['整页涂黑：这一页会全部变黑，AI 读不到任何内容，导出后不可还原。'];
  if (imageLikeFormat()) parts.push('照片 / 场景图里自动识别不到的隐私内容，用这一招最稳。');
  const n = pageEntityCount();
  if (n > 0) parts.push('本页已有 ' + n + ' 处标记，整页涂黑会覆盖本页所有内容。');
  return parts.join(' ');
}
function onWholePageClick() {
  if (!state.jobId || state.busy) return;
  if (ui.btnWholePage && ui.btnWholePage.disabled) return;
  if (!wholePageAvailable()) {
    toast('这个文件是文字排版格式，整页涂黑用不上；请用「框选补漏」标出要隐去的文字。');
    return;
  }
  toast(wholePageConfirmText(), [
    { label: '确认涂黑整页', fn: doWholePageMark },
    { label: '取消', fn: () => toast('已取消，这一页没有被改动。') },
  ], 12000);
}
async function doWholePageMark() {
  if (!state.jobId || state.busy) return;
  state.busy = true;
  const btn = ui.btnWholePage;
  if (btn) { btn.disabled = true; btn.classList.add('busy'); }
  try {
    const res = await api('/api/jobs/' + jobPath() + '/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        page: state.page,
        rect: [0, 0, 1, 1],
        etype: 'OTHER',
        label: WHOLE_PAGE_LABEL,
        propagate: false,
      }),
    });
    toast('整页已加入涂黑，可在右侧清单撤销');
    await refreshLive();
    const eid = res && res.entity && res.entity.id;
    if (eid) flashEntity(eid);
  } catch (err) {
    toast((err && err.human) || '这一页没能整页涂黑，请再试一次。');
  } finally {
    state.busy = false;
    if (btn) { btn.disabled = false; btn.classList.remove('busy'); }
  }
}
async function rejectEntity(ent) {
  if (!ent || !ent.id || state.busy) return;
  state.busy = true;
  try {
    await api('/api/jobs/' + jobPath() + '/entities/' + encodeURIComponent(ent.id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reject' }),
    });
    toast('已撤销「' + labelOf(ent) + '」，这一处不再涂黑。');
    await refreshLive();
  } catch (err) {
    toast((err && err.human) || '这一处没能撤销，请再试一次。');
  } finally {
    state.busy = false;
  }
}

/* ─────────────── 实体清单 / 定位 / 逐条确认 ─────────────── */
function buildRail() {
  clear(ui.railList);
  const groups = new Map();
  state.entities.forEach((ent) => {
    if (!ent || ent.status === 'rejected') return;
    const key = ent.type || 'OTHER';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(ent);
  });
  const keys = Array.from(groups.keys()).sort((a, b) => typeRank(a) - typeRank(b));
  keys.forEach((type) => {
    const items = groups.get(type);
    const group = mk('div', 'grp');
    const head = mk('div', 'grp-h');
    const dot = mk('span', 'cdot');
    dot.style.background = TYPE_COLOR[type] || '#8C8272';
    head.appendChild(dot);
    head.appendChild(document.createTextNode(items[0].label || TYPE_CN[type] || '其他敏感'));
    head.appendChild(mk('span', 'cnt', '×' + items.length));
    group.appendChild(head);
    items.forEach((ent) => {
      const row = mk('div', 'row' + (ent.status === 'pending' ? ' pend' : ''));
      row.dataset.eid = ent.id || '';
      row.appendChild(mk('span', 'raw', ent.value || '—'));
      row.appendChild(mk('span', 'arrow', '→'));
      row.appendChild(mk('span', 'sur', surrogateOf(ent)));
      if (ent.status === 'pending') {
        row.appendChild(mk('span', 'pendtag', '待确认'));
        row.title = '点一下确认这一处';
      } else {
        row.title = '点一下定位';
      }
      // 手动补记/整图涂黑的条目可以撤销（后端 update_entity action=reject 已支持）
      if (ent.origin === 'manual') {
        const undo = mk('button', 'row-undo', '撤销');
        undo.type = 'button';
        undo.title = '撤销这一处，不再涂黑';
        undo.setAttribute('aria-label', '撤销「' + labelOf(ent) + '」');
        undo.addEventListener('click', (ev) => { ev.stopPropagation(); rejectEntity(ent); });
        row.appendChild(undo);
        row.title = '点一下定位 · 手动添加，可撤销';
      }
      row.addEventListener('click', () => onRailRow(ent));
      group.appendChild(row);
    });
    ui.railList.appendChild(group);
  });
  if (!keys.length) ui.railList.appendChild(mk('div', 'rail-empty', '这一份文件没有需要隐去的内容。'));
  ui.railCount.textContent = String(activeEntityCount());
}
function onRailRow(ent) {
  if (!ent) return;
  if (ent.status === 'pending') acceptEntity(ent);
  else locateEntity(ent.id);
}
async function acceptEntity(ent) {
  if (state.busy) return;
  state.busy = true;
  try {
    await api('/api/jobs/' + jobPath() + '/entities/' + encodeURIComponent(ent.id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'accept' }),
    });
    toast('已确认「' + labelOf(ent) + '」这一处，会一起隐去。');
    await refreshLive();
    flashEntity(ent.id);
  } catch (err) {
    toast((err && err.human) || '这一处没能确认，请再试一次。');
  } finally {
    state.busy = false;
  }
}
async function acceptAll() {
  if (state.busy) return;
  state.busy = true;
  try {
    const res = await api('/api/jobs/' + jobPath() + '/accept-all', { method: 'POST' });
    const total = Math.max(num(res && res.total), state.counts.total);
    toast('已全部接受 · 共 ' + total + ' 处会一起隐去。');
    await refreshLive();
  } catch (err) {
    toast((err && err.human) || '没能全部接受，请再试一次。');
  } finally {
    state.busy = false;
  }
}
async function refreshLive() {
  if (!state.jobId) return;
  try {
    const jid = jobPath();
    const [job, ents] = await Promise.all([
      api('/api/jobs/' + jid),
      api('/api/jobs/' + jid + '/entities'),
    ]);
    if (job) state.job = job;
    if (ents && Array.isArray(ents.entities)) state.entities = ents.entities;
    state.counts = readCounts(state.job, state.entities);
    state.pages = Math.max(1, totalPages(state.job));
    state.page = clamp(state.page, 0, state.pages - 1);
    state.previews.orig.delete(state.page);
    state.previews.out.delete(state.page);
    updateConsoleHead();
    updatePager();
    buildRail();
    await loadPage(state.page, { keepScroll: true });
  } catch (err) {
    toast((err && err.human) || '没能刷新当前状态，请再试一次。');
  }
}
async function locateEntity(eid) {
  if (!eid) return;
  let page = state.entityPage.has(eid) ? state.entityPage.get(eid) : null;
  if (page == null) {
    state.previews.orig.forEach((doc, p) => {
      if (page != null) return;
      if (doc && Array.isArray(doc.entities) && doc.entities.some((e) => e.id === eid)) page = p;
    });
  }
  if (page == null) {
    for (let p = 0; p < state.pages; p++) {
      if (state.previews.orig.has(p)) continue;
      try {
        const doc = await api('/api/jobs/' + jobPath() + '/preview/' + p + '?mode=orig');
        state.previews.orig.set(p, doc);
        rememberPreview(doc, p);
        if (doc && Array.isArray(doc.entities) && doc.entities.some((e) => e.id === eid)) { page = p; break; }
      } catch (err) { /* 下一页 */ }
    }
  }
  if (page == null) { toast('这一处不在预览里，可能已经处理掉了。'); return; }
  if (page !== state.page) await loadPage(page);
  flashEntity(eid);
}
function flashEntity(eid) {
  const nodes = $$('[data-eid]').filter((node) => node.dataset.eid === eid);
  if (!nodes.length) return;
  nodes.forEach((node) => {
    const stage = node.closest('.stage');
    if (stage) {
      const nr = node.getBoundingClientRect();
      const sr = stage.getBoundingClientRect();
      if (nr.top < sr.top || nr.bottom > sr.bottom) {
        node.scrollIntoView({ block: 'center', behavior: state.motionReduced ? 'auto' : 'smooth' });
      }
    }
    node.classList.remove('flash'); void node.offsetWidth; node.classList.add('flash');
    setTimeout(() => node.classList.remove('flash'), 2400);
  });
  highlightRail(eid);
}
async function reviewPending() {
  const pending = state.entities.filter((e) => e && e.status === 'pending');
  if (!pending.length) { toast('没有需要确认的，全部都已经处理好了。'); return; }
  await locateEntity(pending[0].id);
  toast('共 ' + pending.length + ' 处没把握。点清单里带「待确认」的行，这一处就算确认。');
}

/* ─────────────── ③ 模式切换 / 严格度 / 翻页 ─────────────── */
function syncModeButtons() {
  $$('.modes button').forEach((btn) => {
    const on = btn.dataset.mode === state.mode;
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', String(on));
  });
}
function setMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  syncModeButtons();
  if (mode === 'black') toast('已切换为「涂黑」形态。这 ' + state.counts.total + ' 处一旦涂黑，就再也找不回来了。');
  else toast('已切换为「换名」形态。AI 读到的是假名字，映射表留在本机。');
  const preview = state.previews.out.get(state.page);
  state.markIdx = 0;
  renderPane('out', preview);
  bindPeek(ui.frameOut);
}
async function changeStrictness(value) {
  if (!value || value === state.strictness) return;
  state.strictness = value;
  if (!state.uploadId) { toast('重新处理需要重新投放一次文件。'); return; }
  closeExportModal(true);
  state.jobId = null;
  resetJobState();
  beginScan(state.fileName);
  setScreen('scan');
  logScan('严格度改为「' + (STRICT_CN[value] || '标准') + '」，重新过一遍。');
  try {
    await createJob();
  } catch (err) {
    scanFailed(err);
  }
}

/* ─────────────── ⑤ 交付 ─────────────── */
function openExportModal() {
  if (!state.jobId) return;
  resetCard(ui.cardAi);
  resetCard(ui.cardHuman);
  ui.btnCloseModal.textContent = state.exported ? '完成' : '先不导出';
  ui.modalExport.classList.add('open');
  setStep(4);
}
function closeExportModal(silent) {
  if (!ui.modalExport.classList.contains('open')) return;
  ui.modalExport.classList.remove('open');
  if (!silent) {
    if (state.exported) setStep(4);
    else setStep(3);
    if (state.screen === 'console') setScreen('console');
  }
}
function resetCard(card) {
  if (!card || card.classList.contains('done')) return;
  const kind = card.dataset.export;
  card.classList.remove('armed', 'busy');
  if (state.cardTpl && state.cardTpl[kind]) card.innerHTML = state.cardTpl[kind];
}
function armHumanCard(card) {
  card.classList.add('armed');
  const cd = $('.cd', card);
  if (cd && !$('.warnline', card)) {
    cd.appendChild(mk('div', 'warnline', '这 ' + state.counts.total + ' 处一旦涂黑，就再也找不回来了。'));
  }
  const doEl = $('.do', card);
  if (doEl) doEl.textContent = '再点一次，确认导出 →';
  toast('涂黑版不可还原：这 ' + state.counts.total + ' 处一旦涂黑，就再也找不回来了。');
}
function onExportCard(card) {
  if (!card || state.modalBusy || card.classList.contains('done')) return;
  const mode = card.dataset.export;
  if (mode === 'human' && !card.classList.contains('armed')) { armHumanCard(card); return; }
  doExport(mode);
}
/* 导出结果里的 verify 是「只增不改」的契约：verified / residual / checked 为数值，
   visual_residual 为数值，media = { count, confirmed }；以后多出来的字段认不出就
   忽略，绝不能因为未知字段把已经成功的交付判成失败。 */
function verifyCount(value) {
  if (value == null) return null;
  if (Array.isArray(value)) return value.length;
  if (typeof value === 'object') {
    if (value.count != null) return num(value.count);
    if (value.unprocessed != null) return num(value.unprocessed);
    return null;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function readVerify(raw) {
  const v = (raw && typeof raw.verify === 'object' && raw.verify) ? raw.verify : {};
  let residual = verifyCount(v.residual);
  if (residual == null) residual = verifyCount(raw && raw.residual);
  let visualResidual = verifyCount(v.visual_residual);
  if (visualResidual == null && v.visual && typeof v.visual === 'object') {
    visualResidual = verifyCount(v.visual.residual);
  }
  let media = null;
  if (v.media && typeof v.media === 'object') {
    const count = verifyCount(v.media);
    if (count != null) media = { count, confirmed: v.media.confirmed === true };
  } else if (v.media != null) {
    const count = verifyCount(v.media);
    if (count != null) media = { count, confirmed: false };
  }
  return { residual, visualResidual, media };
}
function normalizeExport(res) {
  const raw = (res && res.result && typeof res.result === 'object') ? res.result : (res || {});
  const path = raw.path || raw.out || raw.file || '';
  const seal = raw.seal_no || raw.seal || '';
  const sha = raw.sha256 || raw.hash || '';
  const verify = readVerify(raw);
  return {
    path: String(path || ''),
    seal: seal ? String(seal) : '',
    sha: sha ? String(sha) : '',
    residual: verify.residual,
    visualResidual: verify.visualResidual,
    media: verify.media,
    warnings: Array.isArray(raw.warnings) ? raw.warnings : [],
  };
}
function renderExportResult(card, mode, info) {
  clear(card);
  card.classList.add('done');
  card.classList.remove('armed', 'busy');
  const stamp = mk('div', 'bigstamp');
  stamp.appendChild(mk('span', null, '已 隐 去'));
  card.appendChild(stamp);
  const title = mk('div', 'ct', mode === 'ai' ? '给 AI 读' : '给人看');
  title.style.opacity = '.6';
  card.appendChild(title);
  const detail = mk('div', 'cd');
  const fileName = info.path.split(/[\\/]/).pop() || '成品';
  const rows = [
    '已生成 ' + fileName,
    '编号　' + (info.seal || '—'),
    '指纹　' + (info.sha ? info.sha.slice(0, 12) : '—'),
    '自检　' + (info.residual == null ? '已完成' : (info.residual === 0 ? '残留 0 处' : '残留 ' + info.residual + ' 处')),
  ];
  // 新增的复检维度：有就展示，没有就跳过（老版本后端不会返回这两项）
  if (info.visualResidual != null) {
    rows.push('视觉残留　' + (info.visualResidual === 0 ? '0 处' : info.visualResidual + ' 处'));
  }
  if (info.media && info.media.count > 0) {
    rows.push('内嵌媒体　' + info.media.count + ' 处未处理' + (info.media.confirmed ? '（你已确认导出）' : ''));
  }
  rows.push('位置　' + (info.path || '本机输出目录'));
  rows.forEach((line) => detail.appendChild(mk('div', 'res-line', line)));
  if (info.warnings.length) detail.appendChild(mk('div', 'res-line', '另有 ' + info.warnings.length + ' 条提示'));
  card.appendChild(detail);
  const link = document.createElement('a');
  link.className = 'dl';
  link.href = '/api/jobs/' + jobPath() + '/file?mode=' + mode;
  link.setAttribute('download', fileName);
  link.textContent = '下载这一份 ↓';
  card.appendChild(link);
}
/* 内嵌媒体二次确认（docs/red-lines.md E3，后端 jobs.export）：
   首次导出 body 仍只有 { mode }（原逻辑不变）；后端检测到 DOCX/XLSX 里未脱敏的
   内嵌图片/图表时，返回 400 + error.code="media_confirmation_required"。
   用户点「仍要导出」才重发同一 POST，body 加 allow_unprocessed_media:true。 */
function exportRequestBody(mode, allowMedia) {
  const body = { mode };
  if (allowMedia) body.allow_unprocessed_media = true;
  return JSON.stringify(body);
}
function mediaConfirmText(err) {
  const raw = cleanMessage(err && err.human);
  if (!raw) return '这份文件里有未脱敏的内嵌图片，当前版本不会自动处理它们。仍要导出吗？';
  return /[？?]$/.test(raw) ? raw : raw + ' 仍要导出吗？';
}
function askMediaConfirmation(mode, err) {
  toast(mediaConfirmText(err), [
    { label: '仍要导出', fn: () => { doExport(mode, { allowMedia: true }); } },
    { label: '先不导出', fn: () => { toast('已取消导出，原件没有被改动。'); } },
  ], 15000);
}
async function doExport(mode, opts = {}) {
  if (!state.jobId || state.modalBusy) return;
  const allowMedia = !!(opts && opts.allowMedia);
  const card = mode === 'ai' ? ui.cardAi : ui.cardHuman;
  state.modalBusy = true;
  card.classList.add('busy');
  const doEl = $('.do', card);
  if (doEl) doEl.textContent = '正在生成…';
  try {
    const res = await api('/api/jobs/' + jobPath() + '/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: exportRequestBody(mode, allowMedia),
    });
    renderExportResult(card, mode, normalizeExport(res));
    state.exported = true;
    setStep(4);
    ui.btnCloseModal.textContent = '完成';
    toast(mode === 'ai' ? '已交付 · 换名版生成好了，可以喂给 AI。' : '已交付 · 涂黑版生成好了，可以发出去了。');
  } catch (err) {
    resetCard(card);
    if (err && err.code === 'media_confirmation_required') {
      if (allowMedia) {
        // 已确认仍被拒：只能是本机版本没接受这个确认，别让用户反复点
        toast('本机没有接受「仍要导出」的确认，请重启隐站后再试。', null, 8000);
      } else {
        askMediaConfirmation(mode, err);
      }
    } else {
      toast((err && err.human) || '成品没能生成，原件没有被改动。');
    }
  } finally {
    state.modalBusy = false;
    card.classList.remove('busy');
  }
}

/* ─────────────── 启动绑定 ─────────────── */
function cacheDom() {
  [
    'docNo', 'netBadge', 'netText', 'dropzone', 'fileInput', 'btnChoose',
    'scanLines', 'scanProgress', 'scanStage', 'scanCount', 'btnCancelScan',
    'docName', 'cntAll', 'cntDone', 'cntPend', 'btnPrevPage', 'pageLabel', 'btnNextPage',
    'strictSel', 'consoleBody', 'paneOrig', 'paneOut', 'stageOrig', 'stageOut', 'divider',
    'frameOrig', 'frameOut', 'rail', 'railCount', 'railList',
    'btnAcceptAll', 'btnReview', 'reviewCount', 'btnSelect', 'btnExport',
    'footDot', 'footNet', 'footMeta', 'modalExport', 'cardAi', 'cardHuman',
    'btnCloseModal', 'toast', 'toastMsg', 'toastActions',
  ].forEach((id) => { ui[id] = document.getElementById(id); });
  ui.rail = $('.rail');
}
function bindConsole() {
  ui.btnAcceptAll.addEventListener('click', acceptAll);
  ui.btnReview.addEventListener('click', reviewPending);
  ui.btnSelect.addEventListener('click', () => setSelectMode(!state.selectMode));
  ui.btnExport.addEventListener('click', openExportModal);
  ui.btnPrevPage.addEventListener('click', () => loadPage(state.page - 1));
  ui.btnNextPage.addEventListener('click', () => loadPage(state.page + 1));
  ui.strictSel.addEventListener('change', () => changeStrictness(ui.strictSel.value));
  $$('.modes button').forEach((btn) => btn.addEventListener('click', () => setMode(btn.dataset.mode)));
  ui.btnCancelScan.addEventListener('click', () => {
    // 先告诉后端别算了。扫描件一页要好几秒，不喊停它会一直占着 CPU。
    const jid = state.jobId;
    if (jid) {
      api('/api/jobs/' + encodeURIComponent(jid) + '/cancel', { method: 'POST' })
        .catch(() => {});
    }
    stopPolling();
    clearTimeout(state.scanTimer);
    state.scanTyping = false;
    state.scanQueue = [];
    state.finishing = false;
    state.jobId = null;
    setScreen('drop');
    toast('已停下。正在识别的那一页会中断，本机不会留下这个文件。');
  });
  document.addEventListener('pointerdown', (ev) => {
    if (!state.markMenu) return;
    if (ev.target.closest && (ev.target.closest('.menu') || ev.target.closest('.frame'))) return;
    closeMarkMenu();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      closeMarkMenu();
      if (state.selectMode) setSelectMode(false);
      if (ui.modalExport.classList.contains('open')) closeExportModal();
      if (state.markDraft) {
        state.markDraft = null;
        document.body.classList.remove('selecting');
      }
    }
    if (ev.key === 'Alt' && state.screen === 'console') ui.stageOrig.classList.add('alt-select');
    const target = ev.target;
    const editing = target && target.closest && target.closest('input,select,textarea');
    if (state.screen === 'console' && !editing) {
      if (ev.key === 'ArrowLeft') loadPage(state.page - 1);
      if (ev.key === 'ArrowRight') loadPage(state.page + 1);
    }
  });
  document.addEventListener('keyup', (ev) => {
    if (!ev.altKey && !state.selectMode) ui.stageOrig.classList.remove('alt-select');
  });
  window.addEventListener('resize', () => sizeSurLabels(ui.frameOut));
  window.addEventListener('beforeunload', stopPolling);
}
function bindModal() {
  ui.btnCloseModal.addEventListener('click', () => closeExportModal());
  ui.modalExport.addEventListener('click', (ev) => {
    if (ev.target === ui.modalExport) closeExportModal();
  });
  $$('.card[data-export]').forEach((card) => {
    card.addEventListener('click', () => onExportCard(card));
    card.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onExportCard(card); }
    });
  });
}
function init() {
  cacheDom();
  mountWholePageButton();
  mountIdCardMode();
  state.cardTpl = { ai: ui.cardAi.innerHTML, human: ui.cardHuman.innerHTML };
  state.docNo = makeDocNo();
  ui.docNo.textContent = state.docNo;
  bindDropzone();
  bindConsole();
  bindModal();
  bindSyncScroll();
  bindDivider();
  setScreen('drop');
  loadHealth();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
