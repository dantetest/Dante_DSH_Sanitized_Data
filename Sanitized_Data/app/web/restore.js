/* ============================================================
   数据隐站 · AI 回件还原（/restore）
   纯原生 JS，无依赖、无外部网络；只与本机 /api/restore/* 交互。
   流程：投放回件 → 选映射 → 结果（下载 / 再还原一份）。
   安全约束：
     · 服务端返回的一切字符串只用 textContent / createTextNode 写入；
     · 不引入任何外部资源，不写 localStorage（报告含真值，不留存）；
     · 报告只在本机页面展示，页面不会主动外发任何内容。
   契约：docs/impl/restore.md（restore_id / job_id / candidates /
         coverage / outputs[].url / kind 等字段名严格照契约）。
   ============================================================ */
(function () {
  'use strict';

  /* ────────────────────────────────────────────────────────────
     ① DOM 小工具
     ──────────────────────────────────────────────────────────── */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }
  function mk(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function clear(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }
  function num(value) {
    var n = Number(value);
    return isFinite(n) ? n : 0;
  }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function fmtInt(value) { return num(value).toLocaleString('zh-CN'); }
  function fmtSize(value) {
    var n = num(value);
    if (!n || n <= 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }
  function pad2(n) { return n < 10 ? '0' + n : String(n); }
  function fmtTime(value) {
    if (!value) return '时间未知';
    var d = new Date(String(value));
    if (isNaN(d.getTime())) return String(value);
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
      ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }
  function fmtElapsed(value) {
    var ms = Math.max(0, num(value));
    if (ms >= 1000) {
      var s = ms / 1000;
      return (s >= 10 ? Math.round(s) : s.toFixed(1)) + ' 秒';
    }
    return Math.round(ms) + ' 毫秒';
  }
  function coverageValue(value) {
    var n = num(value);
    if (n > 1 && n <= 100) n = n / 100; // 防御：万一后端给了百分数
    return clamp(n, 0, 1);
  }
  function fmtCoverage(value) {
    return Math.round(coverageValue(value) * 100) + '%';
  }
  function prefersReduced() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function scrollToPanel(panel) {
    if (!panel) return;
    var y = (window.pageYOffset || document.documentElement.scrollTop || 0);
    var top = panel.getBoundingClientRect().top + y - 78;
    try {
      window.scrollTo({ top: Math.max(0, top), behavior: prefersReduced() ? 'auto' : 'smooth' });
    } catch (err) {
      window.scrollTo(0, Math.max(0, top));
    }
  }

  /* ────────────────────────────────────────────────────────────
     ② 常量与中文文案
     ──────────────────────────────────────────────────────────── */
  /* 契约 §3 的错误码；未知 code 也一定落到一句人话，不把堆栈给用户 */
  var ERROR_CN = {
    unreachable: '连不上本机的隐站服务，请确认它还在运行。',
    bad_response: '本机回了看不懂的内容，请重新试一次。',
    bad_request: '这一步没走通，请重新选择回件或映射后再试。',
    unsupported_format: '这个格式暂时不支持还原，试试 Word / PDF / Excel / PPT / 图片 / 文本。',
    mapping_not_found: '这份映射已经不在本机了，请重新选择映射。',
    restore_failed: '还原没有成功，回件原件没有被改动，稍后再试一次。',
    ocr_unavailable: '本机 OCR 没有就绪，这份扫描件暂时读不出来。',
    too_large: '回件太大了，先换一个小一点的。',
    forbidden: '这项操作被拦下了，没有改动任何文件。',
    not_found: '本机还没准备好这一步，请升级后再试。',
    service_old: '本机服务版本较旧，请升级后再用回件还原。'
  };
  var FORMAT_CN = {
    txt: '文本', md: 'Markdown', csv: 'CSV', json: 'JSON',
    docx: 'Word', xlsx: 'Excel', pptx: 'PPT', pdf: 'PDF',
    image: '图片', unknown: '未知格式'
  };
  /* 契约 §6：类型标签复用服务端 jobs._TYPE_LABEL；这里做本地兜底，
     未知 etype 按契约显示原文。USCC / RECORD_NO / MEDICAL_RECORD / ACCOUNT
     见契约 §2、§3.1 示例或 vault 映射，_TYPE_LABEL 未列出，故本地补充。
     注意：结果页优先用 result.replacements[].label 建立 etype→label 映射，
     本地兜底只在后端没给 label 时生效，避免前端另编一套与后端冲突的中文名。 */
  var TYPE_CN = {
    PERSON: '人名', PHONE: '电话', ADDRESS: '地址', ID_CARD: '身份证',
    BANK: '账号', ORG: '机构', AMOUNT: '金额', CASE_NO: '案号',
    EMAIL: '邮箱', SECRET: '密钥', OTHER: '其他敏感', LICENSE: '证照号',
    SEAL: '公章/印章', SIGNATURE: '手写签名', SIG_BLOCK: '签署区',
    HANDWRITING: '疑似手写', FACE: '人脸', ID_DOCUMENT: '证件', PLATE: '车牌',
    ID_PHOTO: '证件头像', ID_NAME: '证件姓名', ID_NUMBER: '证件号码',
    ID_ADDRESS: '证件住址', ID_AUTHORITY: '证件签发机关', ID_VALIDITY: '证件有效期',
    ROAD_SIGN: '路牌', DOOR_ADDRESS: '门牌/门址', SHOP_SIGN: '店招/门头',
    ORG_PLAQUE: '单位铭牌', CHAT_AVATAR: '聊天头像', CHAT_NAME: '聊天昵称',
    CHAT_GROUP: '群名称', CHAT_TIME: '聊天时间', CHAT_INPUT: '聊天输入框',
    EMBEDDED_IMAGE: '内嵌图片（敏感）', PASSPORT: '护照号',
    QRCODE: '二维码', BARCODE: '条形码',
    GEO: 'GPS/地理坐标（正射影像）', GPS: 'GPS 位置信息',
    EXIF: 'EXIF 拍摄信息', META: '文件元数据',
    USCC: '统一社会信用代码', RECORD_NO: '住院号', MEDICAL_RECORD: '病历号',
    ACCOUNT: '银行账号'
  };
  function typeLabel(etype) {
    var key = String(etype == null ? '' : etype);
    if (!key) return '其他敏感';
    /* 服务端若已给中文标签（契约 §3.1 by_type 用人话标签），原样透传 */
    return TYPE_CN[key] || key;
  }
  function formatLabel(format) {
    var key = String(format == null ? '' : format);
    if (!key) return '未知格式';
    return FORMAT_CN[key] || key;
  }
  /* 契约 §3.3 的 strategy 是后端内部标识（如 docx/ooxml-text）；
     结果页给用户看时换成中文口径，未知策略按原文透传（不编造新名称）。 */
  var STRATEGY_CN = {
    'docx/ooxml-text': 'Word 文本节点级',
    'xlsx/xml-text': 'Excel 单元格',
    'pptx/ooxml-text': 'PPT 文本节点',
    'pdf/text-layer': 'PDF 文本层重绘',
    'image/ocr-overlay': '图片 OCR 重绘',
    'image/ocr-text': '图片 OCR 文本',
    'text/plain': '纯文本',
    'table/csv-cell': 'CSV 单元格',
    'json/values': 'JSON 值'
  };
  function strategyLabel(strategy) {
    var key = String(strategy == null ? '' : strategy).trim();
    if (!key) return '';
    return STRATEGY_CN[key] || key;
  }

  /* ────────────────────────────────────────────────────────────
     ③ 运行状态与 UI 引用
     ──────────────────────────────────────────────────────────── */
  var state = {
    step: 1,
    upload: null,            // POST /api/restore/upload 的成功响应（已归一化）
    candidates: [],          // 候选映射（按覆盖率降序）
    mappings: [],            // GET /api/restore/mappings 全量
    mappingsState: 'idle',   // idle | loading | ready | old | error
    selectedJobId: '',
    allOpen: false,
    previewOpen: false,
    uploadInFlight: false,
    runBusy: false,
    result: null,
    compare: null,           // 结果页左右对照：{rid,page,total,...}
    uploadFile: null         // 本轮回件 File 对象（旧服务兜底预览用）
  };
  var ui = {};

  function grabUi() {
    [
      'dropzone', 'fileInput', 'btnChoose', 'uploadState', 'uploadText', 'uploadSub',
      'dropError', 'serviceNotice', 'serviceNoticeTitle', 'serviceNoticeText', 'emptyNotice',
      'steps', 'panelDrop', 'panelMap', 'panelResult',
      'fileName', 'fileMeta', 'btnTogglePreview', 'previewBox', 'previewText',
      'mapEmpty', 'candList', 'candCount', 'btnToggleAll', 'allToggleText', 'allWrap',
      'allList', 'allEmpty', 'allCount', 'mapSearch',
      'pickedInfo', 'btnRun', 'mapError', 'btnBackDrop',
      'resultTitle', 'resultHead', 'sumReplaced', 'sumUnique', 'sumWarn', 'sumElapsed',
      'compareBox', 'cmpPrev', 'cmpNext', 'cmpPage', 'cmpLeft', 'cmpRight',
      'cmpNote', 'cmpDownload',
      'typeChips', 'replTable', 'replBody', 'replEmpty', 'truthNote',
      'warnBox', 'warnList', 'warnCount', 'outList',
      'metaFile', 'metaJob', 'metaSeal', 'metaFormat', 'metaStrategy',
      'btnAgainTop', 'btnAgain',
      'netBadge', 'netText', 'footDot', 'footNet', 'pageNo',
      'licenseChip', 'licenseChipText',
      'toast', 'toastMsg', 'toastActions'
    ].forEach(function (id) { ui[id] = document.getElementById(id); });
  }

  /* ────────────────────────────────────────────────────────────
     ④ 本机 API 封装（所有失败都转成人话）
     ──────────────────────────────────────────────────────────── */
  function AppError(code, human) {
    this.code = code;
    this.human = human || ERROR_CN.bad_request;
    this.message = this.human;
  }
  AppError.prototype = Object.create(Error.prototype);
  AppError.prototype.constructor = AppError;

  function cleanMessage(text) {
    var s = String(text || '').replace(/\s+/g, ' ').trim();
    if (!s || /traceback|exception|stack trace|file\s+"|line\s+\d+|\bat\s+0x/i.test(s)) return '';
    return s.length > 120 ? s.slice(0, 120) + '…' : s;
  }
  function humanMessage(data, status) {
    var err = data && data.error;
    var code = err && err.code;
    var msg = cleanMessage(err && err.message);
    if (msg) return msg;
    if (code && ERROR_CN[code]) return ERROR_CN[code];
    if (status === 413) return ERROR_CN.too_large;
    if (status === 404) return ERROR_CN.not_found;
    if (status >= 500) return '本机处理时出了点问题，回件没有被改动。';
    return ERROR_CN.bad_request;
  }
  async function readJson(res) {
    var text = '';
    try { text = await res.text(); } catch (err) { text = ''; }
    var data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (err) { data = null; }
    }
    if (!res.ok) {
      var code = (data && data.error && data.error.code) || ('http_' + res.status);
      throw new AppError(code, humanMessage(data, res.status));
    }
    if (!text) return {};
    if (data === null) throw new AppError('bad_response', ERROR_CN.bad_response);
    return data;
  }
  async function api(path, options) {
    var res;
    try {
      res = await fetch(path, Object.assign({ cache: 'no-store' }, options || {}));
    } catch (err) {
      throw new AppError('unreachable', ERROR_CN.unreachable);
    }
    return readJson(res);
  }
  /* 回件上传：原始字节 body + X-Filename（URL 编码），与 /api/upload 一致 */
  async function uploadRestoreFile(file) {
    var res;
    try {
      res = await fetch('/api/restore/upload', {
        method: 'POST',
        cache: 'no-store',
        headers: {
          'X-Filename': encodeURIComponent(file.name || 'restore.bin'),
          'Content-Type': file.type || 'application/octet-stream'
        },
        body: file
      });
    } catch (err) {
      throw new AppError('unreachable', ERROR_CN.unreachable);
    }
    return readJson(res);
  }

  /* ────────────────────────────────────────────────────────────
     ⑤ Toast / 行内错误提示
     ──────────────────────────────────────────────────────────── */
  var toastTimer = null;
  function hideToast() {
    if (ui.toast) ui.toast.classList.remove('show');
    clearTimeout(toastTimer);
  }
  function toast(message, actions, ms) {
    if (!ui.toast || !ui.toastMsg) return;
    ui.toastMsg.textContent = String(message == null ? '' : message);
    if (ui.toastActions) {
      clear(ui.toastActions);
      (actions || []).forEach(function (action) {
        var btn = mk('button', null, action.label || '好');
        btn.type = 'button';
        btn.addEventListener('click', function () {
          try { if (typeof action.fn === 'function') action.fn(); } finally { hideToast(); }
        });
        ui.toastActions.appendChild(btn);
      });
    }
    ui.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, ms || ((actions && actions.length) ? 6000 : 3600));
  }
  function showError(el, message) {
    if (!el) return;
    if (message) {
      el.textContent = String(message);
      el.hidden = false;
    } else {
      el.textContent = '';
      el.hidden = true;
    }
  }
  function hideAllErrors() {
    showError(ui.dropError);
    showError(ui.mapError);
  }
  /* 旧版服务没有 /api/restore/*（404）：提示升级，但页面其余部分不锁死 */
  function markServiceOld() {
    if (!ui.serviceNotice) return;
    ui.serviceNotice.hidden = false;
    if (ui.serviceNoticeTitle) ui.serviceNoticeTitle.textContent = '本机服务版本较旧';
    if (ui.serviceNoticeText) {
      ui.serviceNoticeText.textContent = '请升级后再用回件还原；页面其余部分仍可用。';
    }
  }
  function isMissingRouteError(err) {
    var code = err && err.code != null ? String(err.code) : '';
    return code === 'not_found' || code === 'http_404';
  }

  /* ────────────────────────────────────────────────────────────
     ⑥ 顶栏状态（health / license / 版本）：失败不影响主流程
     ──────────────────────────────────────────────────────────── */
  async function refreshHealth() {
    var h = null;
    try { h = await api('/api/health'); } catch (err) { h = null; }
    var net = h ? h.net : null;
    var blocked = net === 'blocked' || net === true || !!(net && net.blocked === true);
    if (ui.netBadge) ui.netBadge.classList.toggle('off', !blocked);
    if (ui.netText) {
      ui.netText.textContent = h
        ? (blocked ? '本机处理 · 不联网' : '本机处理 · 未确认断网')
        : '本机服务未连接';
    }
    if (ui.footDot) ui.footDot.className = 'netdot ' + (h ? (blocked ? 'ok' : 'warn') : '');
    if (ui.footNet) {
      ui.footNet.textContent = h
        ? (blocked ? '本机处理 · 不联网' : '本机处理 · 未确认断网')
        : '本机服务未连接';
    }
    if (ui.pageNo) {
      ui.pageNo.textContent = h && h.version ? 'RESTORE · v' + h.version : 'RESTORE · 回件还原';
    }
  }
  async function refreshLicense() {
    var s = null;
    try { s = await api('/api/license/status'); } catch (err) { s = null; }
    if (!ui.licenseChip) return;
    if (!s) { ui.licenseChip.hidden = true; return; }
    var st = s.state || 'free';
    var q = s.quota || {};
    var notEnforced = s.paywall_enabled === false || q.enforced === false;
    var text = '授权';
    if (notEnforced) {
      text = '测试版 · 全功能';
    } else if (st === 'free') {
      text = (s.trial && s.trial.used) ? '试用已结束 · 需购买' : '未授权 · 可开始 3 天试用';
    } else if (st === 'trial') {
      text = '试用 · 剩 ' + num(s.trial && s.trial.days_left) + ' 天';
    } else {
      text = s.edition || '授权';
    }
    if (ui.licenseChipText) ui.licenseChipText.textContent = text;
    ui.licenseChip.className = 'license-chip ' + (notEnforced ? 'paid' : st);
    ui.licenseChip.hidden = false;
  }

  /* ────────────────────────────────────────────────────────────
     ⑦ 三步切换
     ──────────────────────────────────────────────────────────── */
  function setStep(n, opts) {
    state.step = n;
    $$('.step', ui.steps).forEach(function (node) {
      var k = +node.getAttribute('data-step');
      node.classList.toggle('done', k < n);
      node.classList.toggle('active', k === n);
      if (k === n) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
    });
    var panels = [ui.panelDrop, ui.panelMap, ui.panelResult];
    panels.forEach(function (panel, idx) {
      if (panel) panel.hidden = (idx + 1) !== n;
    });
    if (!(opts && opts.noScroll)) {
      if (n === 1) {
        try { window.scrollTo({ top: 0, behavior: prefersReduced() ? 'auto' : 'smooth' }); }
        catch (err) { window.scrollTo(0, 0); }
      } else {
        scrollToPanel(panels[n - 1]);
      }
    }
  }

  /* ────────────────────────────────────────────────────────────
     ⑧ 映射数据：加载 / 归一化 / 展示片段
     ──────────────────────────────────────────────────────────── */
  function normalizeMapping(raw) {
    if (!raw || typeof raw !== 'object') return null;
    var jobId = raw.job_id == null ? '' : String(raw.job_id);
    if (!jobId) return null;
    return {
      job_id: jobId,
      name: raw.name == null || raw.name === '' ? '（未命名映射）' : String(raw.name),
      seal: raw.seal == null ? '' : String(raw.seal),
      saved_at: raw.saved_at == null ? '' : String(raw.saved_at),
      pairs: num(raw.pairs),
      by_type: raw.by_type && typeof raw.by_type === 'object' ? raw.by_type : {},
      hits: num(raw.hits),
      matched: num(raw.matched),
      coverage: coverageValue(raw.coverage)
    };
  }
  async function loadMappings(opts) {
    if (state.mappingsState === 'loading') return state.mappings;
    state.mappingsState = 'loading';
    try {
      var data = await api('/api/restore/mappings');
      var list = data && Array.isArray(data.mappings) ? data.mappings : [];
      state.mappings = list.map(normalizeMapping).filter(function (m) { return !!m; });
      state.mappingsState = 'ready';
      if (ui.serviceNotice) ui.serviceNotice.hidden = true;
      if (ui.emptyNotice) ui.emptyNotice.hidden = state.mappings.length > 0;
    } catch (err) {
      state.mappings = [];
      state.mappingsState = (err && err.code === 'unreachable') ? 'error' : 'old';
      if (ui.serviceNotice) ui.serviceNotice.hidden = false;
      if (ui.serviceNoticeTitle) {
        ui.serviceNoticeTitle.textContent = state.mappingsState === 'error'
          ? '连不上本机服务' : '本机服务版本较旧';
      }
      if (ui.serviceNoticeText) {
        ui.serviceNoticeText.textContent = state.mappingsState === 'error'
          ? '请确认隐站服务还在运行；页面其余部分仍可用。'
          : '请升级后再用回件还原；页面其余部分仍可用。';
      }
      if (ui.emptyNotice) ui.emptyNotice.hidden = true;
    }
    renderAllMappings();
    updateMapEmpty();
    return state.mappings;
  }
  function byCoverage(a, b) {
    if (b.coverage !== a.coverage) return b.coverage - a.coverage;
    return b.hits - a.hits;
  }
  function typeSummaryText(byType) {
    var keys = Object.keys(byType || {});
    if (!keys.length) return '';
    return keys.map(function (k) {
      return typeLabel(k) + ' ' + num(byType[k]);
    }).join(' · ');
  }
  function fileMetaItem(label, value) {
    var span = mk('span', null, label + ' · ');
    span.appendChild(mk('b', null, value));
    return span;
  }

  /* ────────────────────────────────────────────────────────────
     ⑨ 第 1 步：投放回件与上传
     ──────────────────────────────────────────────────────────── */
  function pickFile() {
    if (state.uploadInFlight) {
      toast('正在读取上一份回件，请稍候…');
      return;
    }
    if (ui.fileInput) ui.fileInput.click();
  }
  function handleFiles(files) {
    if (!files || !files.length) return;
    if (state.uploadInFlight) {
      toast('正在读取上一份回件，请稍候…');
      return;
    }
    var file = files[0];
    if (!file) return;
    if (!file.size) {
      showError(ui.dropError, '这个文件是空的，换一份试试。');
      toast('这个文件是空的，换一份试试。');
      return;
    }
    if (files.length > 1) toast('一次只还原一份回件，已取第一份。');
    startUpload(file);
  }
  async function startUpload(file) {
    state.uploadInFlight = true;
    hideAllErrors();
    if (ui.uploadState) ui.uploadState.hidden = false;
    if (ui.uploadText) ui.uploadText.textContent = '正在读取回件并匹配映射…';
    if (ui.uploadSub) ui.uploadSub.textContent = '图片 / 扫描件需要 OCR，可能要等几秒。';
    if (ui.dropzone) ui.dropzone.classList.add('busy');
    var slowTimer = setTimeout(function () {
      if (ui.uploadSub) ui.uploadSub.textContent = '还在读取：扫描件 OCR 比较慢，请再等一下…';
    }, 6000);
    try {
      var data = await uploadRestoreFile(file);
      applyUpload(data, file);
      setStep(2);
      toast('回件已读入，本机匹配到 ' + state.candidates.length + ' 条候选映射。');
    } catch (err) {
      var msg = (err && err.human) || ERROR_CN.bad_request;
      if (isMissingRouteError(err)) markServiceOld();
      if (err && err.code === 'unreachable') refreshHealth();
      showError(ui.dropError, msg);
      toast(msg);
    } finally {
      clearTimeout(slowTimer);
      state.uploadInFlight = false;
      if (ui.uploadState) ui.uploadState.hidden = true;
      if (ui.dropzone) ui.dropzone.classList.remove('busy');
      if (ui.fileInput) ui.fileInput.value = '';
    }
  }
  function applyUpload(data, file) {
    var upload = {
      restore_id: data && data.restore_id != null ? String(data.restore_id) : '',
      name: data && data.name ? String(data.name) : ((file && file.name) || '回件'),
      format: data && data.format ? String(data.format) : 'unknown',
      bytes: num(data && data.bytes) || (file ? file.size : 0),
      sha256: data && data.sha256 ? String(data.sha256) : '',
      chars: num(data && data.chars),
      preview: data && data.preview ? String(data.preview) : '',
      candidates: [],
      candidates_total: num(data && data.candidates_total),
      notes: data && Array.isArray(data.notes) ? data.notes.map(function (n) { return String(n); }) : []
    };
    if (!upload.restore_id) throw new AppError('bad_response', ERROR_CN.bad_response);
    if (!upload.format) upload.format = 'unknown';
    var rawCands = data && Array.isArray(data.candidates) ? data.candidates : [];
    upload.candidates = rawCands.map(normalizeMapping).filter(function (m) { return !!m; });
    state.upload = upload;
    state.uploadFile = file || null;
    state.candidates = upload.candidates.slice().sort(byCoverage);
    state.selectedJobId = state.candidates.length ? state.candidates[0].job_id : '';
    state.result = null;

    renderFileCard(upload);
    renderCandidates();
    setPreviewOpen(false);
    setAllOpen(false);
    syncSelectionUI();
    if (!state.candidates.length && state.mappingsState !== 'ready') {
      loadMappings({ quiet: true }).then(function () {
        renderCandidates();
        syncSelectionUI();
      });
    }
  }
  function renderFileCard(upload) {
    if (ui.fileName) ui.fileName.textContent = upload.name;
    if (!ui.fileMeta) return;
    clear(ui.fileMeta);
    ui.fileMeta.appendChild(fileMetaItem('格式', formatLabel(upload.format)));
    ui.fileMeta.appendChild(fileMetaItem('大小', fmtSize(upload.bytes) || '—'));
    ui.fileMeta.appendChild(fileMetaItem('提取字符', fmtInt(upload.chars)));
    if (upload.sha256) ui.fileMeta.appendChild(fileMetaItem('指纹', upload.sha256.slice(0, 12)));
    if (upload.candidates_total) {
      ui.fileMeta.appendChild(fileMetaItem('候选映射', upload.candidates_total + ' 条'));
    }
    upload.notes.forEach(function (note) {
      var span = mk('span', 'file-note', '提示 · ' + note);
      ui.fileMeta.appendChild(span);
    });
  }
  function setPreviewOpen(open) {
    state.previewOpen = !!open;
    if (ui.previewBox) ui.previewBox.hidden = !state.previewOpen;
    if (ui.btnTogglePreview) {
      var hasPreview = !!(state.upload && state.upload.preview);
      ui.btnTogglePreview.hidden = !hasPreview;
      ui.btnTogglePreview.setAttribute('aria-expanded', String(state.previewOpen));
      ui.btnTogglePreview.textContent = state.previewOpen ? '收起预览' : '看提取预览';
    }
    if (state.previewOpen && ui.previewText) {
      ui.previewText.textContent = (state.upload && state.upload.preview) || '';
    }
  }

  /* ────────────────────────────────────────────────────────────
     ⑩ 第 2 步：候选映射 / 全部映射 / 选择
     ──────────────────────────────────────────────────────────── */
  function renderCandidates() {
    if (!ui.candList) return;
    clear(ui.candList);
    var cands = state.candidates;
    if (ui.candCount) ui.candCount.textContent = String(cands.length);

    cands.forEach(function (m, index) {
      var card = mk('label', 'map-card');
      var input = document.createElement('input');
      input.type = 'radio';
      input.name = 'mappingPick';
      input.value = m.job_id;
      input.addEventListener('change', function () { selectMapping(m.job_id); });
      card.appendChild(input);

      if (index === 0) card.appendChild(mk('span', 'best-tag', '最匹配'));
      card.appendChild(mk('div', 'map-name', m.name));

      var meta = mk('div', 'map-meta');
      meta.appendChild(mk('span', null, '封条 ' + (m.seal || '—')));
      meta.appendChild(mk('span', null, '时间 ' + fmtTime(m.saved_at)));
      card.appendChild(meta);

      var stats = mk('div', 'map-stats');
      var pairsItem = mk('span', null, '实体对 ');
      pairsItem.appendChild(mk('b', null, fmtInt(m.pairs)));
      stats.appendChild(pairsItem);
      var hitsItem = mk('span', null, '命中 ');
      hitsItem.appendChild(mk('b', null, fmtInt(m.hits) + ' 处'));
      stats.appendChild(hitsItem);
      var matchedItem = mk('span', null, '命中假值 ');
      matchedItem.appendChild(mk('b', null, fmtInt(m.matched) + ' / ' + fmtInt(m.pairs)));
      stats.appendChild(matchedItem);
      card.appendChild(stats);

      var types = typeSummaryText(m.by_type);
      if (types) card.appendChild(mk('div', 'map-meta', '类型 ' + types));

      var bar = mk('div', 'cov-bar');
      var fill = mk('i');
      fill.style.width = (coverageValue(m.coverage) * 100).toFixed(1) + '%';
      bar.appendChild(fill);
      card.appendChild(bar);

      var cov = mk('div', 'cov-label');
      cov.appendChild(mk('span', null, '覆盖率'));
      cov.appendChild(mk('b', null, fmtCoverage(m.coverage)));
      card.appendChild(cov);

      ui.candList.appendChild(card);
    });

    if (!cands.length && state.mappings.length) setAllOpen(true);
    updateMapEmpty();
    syncSelectionUI();
  }
  function updateMapEmpty() {
    if (!ui.mapEmpty) return;
    if (state.candidates.length) {
      ui.mapEmpty.hidden = true;
      return;
    }
    ui.mapEmpty.hidden = false;
    var title = $('.empty-title', ui.mapEmpty);
    var text = $('p', ui.mapEmpty);
    if (!state.mappings.length) {
      if (title) title.textContent = '还没有可还原的映射';
      if (text) text.textContent = '先用「换名版」把文件交付给 AI，AI 交回后再到这里换回真值。';
    } else {
      if (title) title.textContent = '没有找到命中的映射';
      if (text) text.textContent = '回件里可能没有出现映射中的假值，请展开「全部映射」手动选择。';
    }
  }
  function renderAllMappings() {
    if (!ui.allList) return;
    clear(ui.allList);
    var list = state.mappings;
    if (ui.allCount) ui.allCount.textContent = list.length ? '（' + list.length + ' 条）' : '（0 条）';
    list.forEach(function (m) {
      /* 「全部映射」的行用 button 而不是 radio：候选映射已是同一 radio 组，
         两处重复的 radio 会在同一组里互相取消勾选，这里改用按钮点击选中。 */
      var row = mk('button', 'map-row');
      row.type = 'button';
      row.setAttribute('aria-pressed', 'false');
      row.setAttribute('data-job', m.job_id);
      row.addEventListener('click', function () { selectMapping(m.job_id); });

      row.appendChild(mk('span', 'rname', m.name));
      row.appendChild(mk('span', 'rmeta',
        [m.seal ? '封条 ' + m.seal : '封条 —', fmtTime(m.saved_at), '实体对 ' + m.pairs].join(' · ')));
      var types = mk('span', 'rtypes');
      Object.keys(m.by_type || {}).forEach(function (key) {
        types.appendChild(mk('span', 'rtype', typeLabel(key) + ' ' + fmtInt(m.by_type[key])));
      });
      if (types.firstChild) row.appendChild(types);
      row.setAttribute('data-search',
        (m.name + ' ' + m.seal + ' ' + m.job_id + ' ' + m.saved_at).toLowerCase());
      ui.allList.appendChild(row);
    });
    filterAllMappings();
    syncSelectionUI();
  }
  function filterAllMappings() {
    if (!ui.allList) return;
    var query = (ui.mapSearch && ui.mapSearch.value ? ui.mapSearch.value : '').trim().toLowerCase();
    var rows = $$('.map-row', ui.allList);
    var shown = 0;
    rows.forEach(function (row) {
      var hay = row.getAttribute('data-search') || '';
      var hit = !query || hay.indexOf(query) !== -1;
      row.hidden = !hit;
      if (hit) shown++;
    });
    if (ui.allEmpty) {
      if (!rows.length) {
        ui.allEmpty.textContent = state.mappingsState === 'old'
          ? '本机映射列表不可用：服务版本较旧，请升级后再试。'
          : (state.mappingsState === 'error'
            ? '读不到映射：请确认本机服务还在运行。'
            : '暂时没有可用的映射。');
        ui.allEmpty.hidden = false;
      } else {
        ui.allEmpty.textContent = '没有匹配的映射';
        ui.allEmpty.hidden = shown > 0;
      }
    }
  }
  function setAllOpen(open) {
    state.allOpen = !!open;
    if (ui.allWrap) ui.allWrap.hidden = !state.allOpen;
    if (ui.btnToggleAll) ui.btnToggleAll.setAttribute('aria-expanded', String(state.allOpen));
    if (ui.allToggleText) ui.allToggleText.textContent = state.allOpen ? '收起全部映射' : '展开全部映射';
  }
  async function toggleAll() {
    var open = !state.allOpen;
    if (open && state.mappingsState !== 'ready') await loadMappings({ quiet: true });
    setAllOpen(open);
    if (open && ui.mapSearch) {
      try { ui.mapSearch.focus(); } catch (err) { /* 忽略 */ }
    }
  }
  function findMapping(jobId) {
    var found = null;
    state.candidates.concat(state.mappings).some(function (m) {
      if (m.job_id === jobId) { found = m; return true; }
      return false;
    });
    return found;
  }
  function selectMapping(jobId) {
    state.selectedJobId = jobId == null ? '' : String(jobId);
    syncSelectionUI();
  }
  function syncSelectionUI() {
    var id = state.selectedJobId;
    /* 只有候选映射是 radio；「全部映射」的行是 button，用 class / aria-pressed 表示选中 */
    $$('input[name="mappingPick"]').forEach(function (input) {
      input.checked = !!id && input.value === id;
    });
    $$('.map-card', ui.candList).forEach(function (card) {
      var input = card.querySelector('input');
      card.classList.toggle('on', !!(input && input.checked));
    });
    $$('.map-row', ui.allList).forEach(function (row) {
      var on = !!id && row.getAttribute('data-job') === id;
      row.classList.toggle('on', on);
      row.setAttribute('aria-pressed', String(on));
    });
    var picked = id ? findMapping(id) : null;
    if (ui.pickedInfo) {
      clear(ui.pickedInfo);
      if (picked) {
        ui.pickedInfo.appendChild(document.createTextNode('已选映射：'));
        ui.pickedInfo.appendChild(mk('b', null, picked.name));
        ui.pickedInfo.appendChild(document.createTextNode(
          ' · 封条 ' + (picked.seal || '—') + ' · 实体对 ' + picked.pairs));
      } else {
        ui.pickedInfo.textContent = '还没有选择映射';
      }
    }
    if (ui.btnRun) {
      ui.btnRun.disabled = !picked || state.runBusy || !state.upload || !state.upload.restore_id;
    }
  }

  /* ────────────────────────────────────────────────────────────
     ⑪ 开始还原（POST /api/restore/run）
     ──────────────────────────────────────────────────────────── */
  async function runRestore() {
    if (state.runBusy) return;
    var upload = state.upload;
    var jobId = state.selectedJobId;
    if (!upload || !upload.restore_id) {
      toast('请先投放回件。');
      setStep(1);
      return;
    }
    if (!jobId) {
      toast('请先选择一条映射。');
      return;
    }
    state.runBusy = true;
    showError(ui.mapError);
    if (ui.btnRun) {
      ui.btnRun.disabled = true;
      ui.btnRun.textContent = '正在还原…';
    }
    try {
      var data = await api('/api/restore/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ restore_id: upload.restore_id, job_id: jobId })
      });
      state.result = data || {};
      renderResult(state.result);
      setStep(3);
      if (ui.resultTitle) {
        try { ui.resultTitle.focus({ preventScroll: true }); }
        catch (err) { try { ui.resultTitle.focus(); } catch (err2) { /* 忽略 */ } }
      }
      toast('还原完成，成品已写到本机 data/out/。');
    } catch (err) {
      var msg = (err && err.human) || ERROR_CN.restore_failed;
      if (isMissingRouteError(err)) markServiceOld();
      if (err && err.code === 'unreachable') refreshHealth();
      showError(ui.mapError, msg);
      toast(msg);
      if (err && err.code === 'mapping_not_found') {
        loadMappings({ quiet: true }).then(function () {
          renderAllMappings();
          renderCandidates();
        });
      }
    } finally {
      state.runBusy = false;
      if (ui.btnRun) ui.btnRun.textContent = '开始还原';
      syncSelectionUI();
    }
  }

  /* ────────────────────────────────────────────────────────────
     ⑫ 第 3 步：结果渲染与下载
     ──────────────────────────────────────────────────────────── */
  function uniqueFromReplacements(replacements) {
    /* 防御旧服务没给 counts.unique_replaced：按假值去重，而不是按行数 */
    var seen = {};
    var count = 0;
    replacements.forEach(function (item) {
      var fake = item && item.fake != null ? String(item.fake) : '';
      if (!fake || seen[fake]) return;
      seen[fake] = true;
      count++;
    });
    return count;
  }
  function safeOutputUrl(rawUrl, restoreId, kind) {
    var fallbackKind = (kind === 'text') ? 'text' : 'main';
    if (typeof rawUrl === 'string' && rawUrl) {
      try {
        var url = new URL(rawUrl, window.location.href);
        if (url.origin === window.location.origin && (url.protocol === 'http:' || url.protocol === 'https:')) {
          return url.pathname + url.search;
        }
      } catch (err) { /* 地址不合法：按契约兜底拼 */ }
    }
    if (!restoreId) return '';
    return '/api/restore/file?rid=' + encodeURIComponent(restoreId) + '&kind=' + fallbackKind;
  }
  function renderResult(data) {
    var counts = data.counts && typeof data.counts === 'object' ? data.counts : {};
    var replacements = data.replacements && Array.isArray(data.replacements) ? data.replacements : [];
    var warnings = data.warnings && Array.isArray(data.warnings) ? data.warnings : [];
    var outputs = data.outputs && Array.isArray(data.outputs) ? data.outputs : [];
    var byType = data.by_type && typeof data.by_type === 'object' ? data.by_type : {};

    if (ui.resultHead) {
      ui.resultHead.textContent = replacements.length
        ? '已在本机完成还原，报告含真值，仅限本机查看。'
        : '已在本机完成处理，这次没有替换任何假值。';
    }

    /* 汇总 */
    var unique = counts.unique_replaced != null
      ? num(counts.unique_replaced)
      : uniqueFromReplacements(replacements);
    if (ui.sumReplaced) ui.sumReplaced.textContent = fmtInt(counts.replaced);
    if (ui.sumUnique) ui.sumUnique.textContent = fmtInt(unique);
    if (ui.sumWarn) ui.sumWarn.textContent = fmtInt(warnings.length || counts.warnings);
    if (ui.sumElapsed) ui.sumElapsed.textContent = fmtElapsed(data.elapsed_ms);

    /* 按类型统计（by_type 缺失时从替换明细反推，防御旧服务）。
       芯片标签优先用替换明细里的 label（后端口径），保证和下面明细表一致；
       后端没给 label 时才退回本地兜底表，避免前端另编一套中文名。 */
    if (ui.typeChips) {
      clear(ui.typeChips);
      var labelByType = {};
      replacements.forEach(function (item) {
        if (!item || item.etype == null) return;
        var etypeKey = String(item.etype);
        var itemLabel = item.label != null ? String(item.label) : '';
        if (itemLabel && !labelByType[etypeKey]) labelByType[etypeKey] = itemLabel;
      });
      var keys = Object.keys(byType);
      if (!keys.length) {
        replacements.forEach(function (item) {
          var key = item && item.etype != null ? String(item.etype) : 'OTHER';
          byType[key] = num(byType[key]) + num(item && item.count);
        });
        keys = Object.keys(byType);
      }
      keys.sort(function (a, b) { return num(byType[b]) - num(byType[a]); });
      keys.forEach(function (key) {
        var chip = mk('span', 'tchip', (labelByType[key] || typeLabel(key)) + ' ');
        chip.appendChild(mk('b', null, fmtInt(byType[key])));
        ui.typeChips.appendChild(chip);
      });
    }

    /* 替换明细（所有值都走 textContent） */
    if (ui.replBody) {
      clear(ui.replBody);
      replacements.forEach(function (item) {
        if (!item || typeof item !== 'object') return;
        var tr = document.createElement('tr');
        var etype = item.etype != null ? String(item.etype) : '';
        var label = item.label != null && item.label !== '' ? String(item.label) : typeLabel(etype);
        tr.appendChild(mk('td', 'td-type', label));
        tr.appendChild(mk('td', 'td-fake', item.fake != null ? String(item.fake) : ''));
        tr.appendChild(mk('td', 'td-real', item.orig != null ? String(item.orig) : ''));
        tr.appendChild(mk('td', 'td-count', fmtInt(item.count)));
        ui.replBody.appendChild(tr);
      });
    }
    if (ui.replEmpty) ui.replEmpty.hidden = replacements.length > 0;
    if (ui.replTable) ui.replTable.hidden = replacements.length === 0;
    if (ui.truthNote) ui.truthNote.hidden = replacements.length === 0;

    /* 告警：琥珀色，不用红色恐吓 */
    if (ui.warnList) {
      clear(ui.warnList);
      warnings.forEach(function (w) {
        ui.warnList.appendChild(mk('li', null, String(w)));
      });
    }
    if (ui.warnBox) ui.warnBox.hidden = warnings.length === 0;
    if (ui.warnCount) ui.warnCount.textContent = warnings.length ? '（' + warnings.length + ' 条）' : '';

    /* 成品下载：kind=main 主按钮，kind=text 次按钮 */
    if (ui.outList) {
      clear(ui.outList);
      var order = ['main', 'text'];
      var items = outputs.filter(function (o) { return o && typeof o === 'object'; }).slice();
      items.sort(function (a, b) {
        var ia = order.indexOf(String(a.kind));
        var ib = order.indexOf(String(b.kind));
        return (ia < 0 ? 9 : ia) - (ib < 0 ? 9 : ib);
      });
      var restoreId = data.restore_id != null ? String(data.restore_id)
        : (state.upload && state.upload.restore_id) || '';
      var rendered = 0;
      items.forEach(function (out) {
        var kind = out.kind != null ? String(out.kind) : '';
        var href = safeOutputUrl(out.url, restoreId, kind);
        if (!href) return;
        var name = out.name != null && out.name !== '' ? String(out.name)
          : (kind === 'text' ? '还原文本.txt' : '还原成品');
        var link = document.createElement('a');
        link.className = 'btn dl-btn ' + (kind === 'main' ? 'primary' : 'ghost');
        link.href = href;
        link.setAttribute('download', name);
        link.appendChild(mk('span', 'dl-kind',
          kind === 'main' ? '主成品' : (kind === 'text' ? '还原文本' : '附件')));
        link.appendChild(mk('span', 'dl-name', name + (num(out.bytes) ? ' · ' + fmtSize(out.bytes) : '')));
        ui.outList.appendChild(link);
        rendered++;
      });
      if (!rendered) {
        ui.outList.appendChild(mk('div', 'out-empty', '这次没有生成成品文件，请再还原一次。'));
      }
    }

    /* 本次映射信息 */
    if (ui.metaFile) ui.metaFile.textContent = (state.upload && state.upload.name) || '—';
    if (ui.metaJob) ui.metaJob.textContent = data.job_name ? String(data.job_name) : '—';
    if (ui.metaSeal) ui.metaSeal.textContent = data.seal ? String(data.seal) : '—';
    if (ui.metaFormat) ui.metaFormat.textContent = formatLabel(data.format || (state.upload && state.upload.format));
    if (ui.metaStrategy) {
      var strategyText = strategyLabel(data.strategy);
      ui.metaStrategy.textContent = strategyText || '—';
    }

    /* 左右对照预览：AI 回件 ↔ 还原稿（PDF/图片支持，其它格式降级提示） */
    renderCompare(data);
  }

  /* ── 结果页左右对照预览（左：AI 回件；右：还原稿） ── */
  function previewableByName(name) {
    return /\.(pdf|png|jpe?g|webp|gif|bmp)$/i.test(String(name || ''));
  }
  function revokeCompareUrls(cmp) {
    if (!cmp || !cmp.objectUrls) return;
    cmp.objectUrls.forEach(function (u) {
      try { URL.revokeObjectURL(u); } catch (err) { /* 忽略 */ }
    });
    cmp.objectUrls = [];
  }
  function renderCompare(data) {
    var box = ui.compareBox;
    if (!box) return;
    revokeCompareUrls(state.compare);
    var meta = data && data.preview;
    var restoreId = data.restore_id != null ? String(data.restore_id)
      : (state.upload && state.upload.restore_id) || '';
    var main = null;
    (data.outputs || []).forEach(function (o) {
      if (!main && o && String(o.kind) === 'main') main = o;
    });
    var inName = (state.upload && state.upload.name) || '';
    var outName = main ? String(main.name || '') : '';
    var apiIn = !!(meta && meta.input && meta.input.renderable);
    var apiOut = !!(meta && meta.output && meta.output.renderable);
    var inOk = apiIn || previewableByName(inName);
    var outOk = apiOut || previewableByName(outName);
    if (!inOk && !outOk) {
      box.hidden = true;
      state.compare = null;
      return;
    }
    var pageMode = apiIn || apiOut;   // 服务端逐页对照是否可用
    var pages = pageMode
      ? Math.max(1, num(meta && meta.input && meta.input.pages),
                 num(meta && meta.output && meta.output.pages))
      : 1;
    state.compare = {
      rid: restoreId, page: 0, total: pages, pageMode: pageMode,
      input: (meta && meta.input) || { renderable: false, pages: 0, format: '' },
      output: (meta && meta.output) || { renderable: false, pages: 0, format: '' },
      inOk: inOk, outOk: outOk, main: main, objectUrls: []
    };
    box.hidden = false;
    /* 翻页控件只在逐页模式可用 */
    [ui.cmpPrev, ui.cmpNext, ui.cmpPage].forEach(function (node) {
      if (node) node.hidden = !pageMode;
    });
    var notes = [];
    if (!pageMode) {
      notes.push('当前服务为旧版本，暂不支持逐页对照；已用浏览器内置预览展示整份文件（可滚动查看）。升级后可逐页对照/翻页。');
    }
    if (!inOk) notes.push('回件格式暂不支持页面预览，请下载回件查看。');
    if (!outOk) notes.push('还原稿格式暂不支持页面预览，请下载主成品查看。');
    if (ui.cmpNote) {
      ui.cmpNote.textContent = notes.join(' ');
      ui.cmpNote.hidden = notes.length === 0;
    }
    if (ui.cmpDownload) {
      var href = main ? safeOutputUrl(main.url, restoreId, 'main') : '';
      if (href) {
        ui.cmpDownload.href = href;
        ui.cmpDownload.setAttribute('download',
          main.name != null && main.name !== '' ? String(main.name) : '还原成品');
        ui.cmpDownload.hidden = false;
      } else {
        ui.cmpDownload.hidden = true;
      }
    }
    if (pageMode) loadComparePage(0);
    else renderFallbackCompare();
  }

  function loadComparePage(page) {
    var cmp = state.compare;
    if (!cmp || !cmp.pageMode) return;
    var total = Math.max(1, cmp.total);
    cmp.page = clamp(num(page), 0, total - 1);
    if (ui.cmpPage) ui.cmpPage.textContent = (cmp.page + 1) + ' / ' + total;
    if (ui.cmpPrev) ui.cmpPrev.disabled = cmp.page <= 0;
    if (ui.cmpNext) ui.cmpNext.disabled = cmp.page >= total - 1;
    loadCompareSide('cmpLeft', cmp.inOk, cmp.input, cmp.page);
    loadCompareSide('cmpRight', cmp.outOk, cmp.output, cmp.page);
  }

  function loadCompareSide(key, ok, meta, page) {
    var el = ui[key];
    if (!el) return;
    clear(el);
    if (!ok) {
      el.appendChild(mk('div', 'cmp-hint', '该格式暂不支持页面对照预览，请下载文件后查看。'));
      return;
    }
    if (page >= num(meta && meta.pages)) {
      el.appendChild(mk('div', 'cmp-hint', '此页超出该文件页数。'));
      return;
    }
    var img = document.createElement('img');
    img.alt = key === 'cmpLeft' ? 'AI 回件页面' : '还原稿页面';
    img.loading = 'lazy';
    img.src = '/api/restore/render?rid=' + encodeURIComponent(state.compare.rid) +
      '&side=' + (key === 'cmpLeft' ? 'input' : 'output') + '&page=' + page;
    img.onerror = function () {
      /* 旧服务没有逐页渲染接口：降级为浏览器内置预览 */
      if (state.compare) state.compare.pageMode = false;
      [ui.cmpPrev, ui.cmpNext, ui.cmpPage].forEach(function (node) { if (node) node.hidden = true; });
      if (ui.cmpNote) {
        ui.cmpNote.textContent = '逐页对照接口不可用，已切换为浏览器内置预览（可滚动查看整份文件）。';
        ui.cmpNote.hidden = false;
      }
      renderFallbackCompare();
    };
    el.appendChild(img);
  }

  /* 旧服务/非逐页格式兜底：左用本地文件对象，右拉取主成品 */
  function renderFallbackCompare() {
    fallbackCompareSide('cmpLeft');
    fallbackCompareSide('cmpRight');
  }
  function fallbackCompareSide(key) {
    var cmp = state.compare;
    var el = ui[key];
    if (!cmp || !el) return;
    clear(el);
    var name = key === 'cmpLeft'
      ? ((state.upload && state.upload.name) || '')
      : ((cmp.main && cmp.main.name) || '');
    if (!previewableByName(name)) {
      el.appendChild(mk('div', 'cmp-hint', '该格式暂不支持页面预览，请下载文件后查看。'));
      return;
    }
    var hint = mk('div', 'cmp-hint', '正在载入浏览器内置预览…');
    el.appendChild(hint);
    var show = function (blob) {
      clear(el);
      var objUrl = URL.createObjectURL(blob);
      cmp.objectUrls = cmp.objectUrls || [];
      cmp.objectUrls.push(objUrl);
      var ext = (String(name).split('.').pop() || '').toLowerCase();
      if (ext === 'pdf') {
        var embed = document.createElement('embed');
        embed.type = 'application/pdf';
        embed.src = objUrl;
        embed.className = 'cmp-doc';
        el.appendChild(embed);
      } else {
        var img = document.createElement('img');
        img.src = objUrl;
        img.alt = name;
        img.className = 'cmp-doc-img';
        el.appendChild(img);
      }
    };
    if (key === 'cmpLeft') {
      var file = state.uploadFile;
      if (file) show(file);
      else { clear(el); el.appendChild(mk('div', 'cmp-hint', '回件已不在内存，请下载回件查看。')); }
      return;
    }
    var url = cmp.main ? safeOutputUrl(cmp.main.url, cmp.rid, 'main') : '';
    if (!url) {
      clear(el);
      el.appendChild(mk('div', 'cmp-hint', '没有可预览的还原稿，请重新还原。'));
      return;
    }
    fetch(url, { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('http ' + res.status);
        return res.blob();
      })
      .then(show)
      .catch(function () {
        clear(el);
        el.appendChild(mk('div', 'cmp-hint', '预览加载失败，请下载文件查看。'));
      });
  }

  /* ────────────────────────────────────────────────────────────
     ⑬ 再还原一份 / 重置
     ──────────────────────────────────────────────────────────── */
  function resetAll() {
    revokeCompareUrls(state.compare);
    state.upload = null;
    state.uploadFile = null;
    state.candidates = [];
    state.selectedJobId = '';
    state.result = null;
    state.compare = null;
    state.runBusy = false;
    state.uploadInFlight = false;
    hideAllErrors();
    if (ui.uploadState) ui.uploadState.hidden = true;
    if (ui.uploadText) ui.uploadText.textContent = '正在读取回件并匹配映射…';
    if (ui.uploadSub) ui.uploadSub.textContent = '图片 / 扫描件需要 OCR，可能要等几秒。';
    if (ui.fileInput) ui.fileInput.value = '';
    if (ui.dropzone) ui.dropzone.classList.remove('busy', 'hot');
    if (ui.fileName) ui.fileName.textContent = '—';
    if (ui.fileMeta) clear(ui.fileMeta);
    if (ui.candList) clear(ui.candList);
    if (ui.candCount) ui.candCount.textContent = '0';
    if (ui.mapEmpty) ui.mapEmpty.hidden = true;
    if (ui.previewText) ui.previewText.textContent = '';
    setPreviewOpen(false);
    setAllOpen(false);
    if (ui.mapSearch) ui.mapSearch.value = '';
    if (ui.pickedInfo) ui.pickedInfo.textContent = '还没有选择映射';
    if (ui.btnRun) {
      ui.btnRun.textContent = '开始还原';
      ui.btnRun.disabled = true;
    }
    if (ui.resultHead) ui.resultHead.textContent = '已在本机完成还原。';
    if (ui.replBody) clear(ui.replBody);
    if (ui.typeChips) clear(ui.typeChips);
    if (ui.truthNote) ui.truthNote.hidden = false;
    if (ui.outList) clear(ui.outList);
    if (ui.compareBox) ui.compareBox.hidden = true;
    if (ui.cmpLeft) clear(ui.cmpLeft);
    if (ui.cmpRight) clear(ui.cmpRight);
    if (ui.cmpPage) ui.cmpPage.textContent = '1 / 1';
    if (ui.cmpNote) { ui.cmpNote.hidden = true; ui.cmpNote.textContent = ''; }
    if (ui.warnList) clear(ui.warnList);
    if (ui.warnBox) ui.warnBox.hidden = true;
    setStep(1, { noScroll: true });
    try { window.scrollTo({ top: 0, behavior: prefersReduced() ? 'auto' : 'smooth' }); }
    catch (err) { window.scrollTo(0, 0); }
    if (ui.dropzone) {
      try { ui.dropzone.focus({ preventScroll: true }); } catch (err) { /* 忽略 */ }
    }
    /* 重新拉一次映射，避免刚导出/删除的映射对不上（失败已有人话提示） */
    loadMappings({ quiet: true }).then(function () {
      updateMapEmpty();
      syncSelectionUI();
    });
  }

  /* ────────────────────────────────────────────────────────────
     ⑭ 事件绑定 / 启动
     ──────────────────────────────────────────────────────────── */
  function bind() {
    if (ui.dropzone) {
      ui.dropzone.addEventListener('click', pickFile);
      ui.dropzone.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
          ev.preventDefault();
          pickFile();
        }
      });
      ['dragenter', 'dragover'].forEach(function (name) {
        ui.dropzone.addEventListener(name, function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          ui.dropzone.classList.add('hot');
        });
      });
      ui.dropzone.addEventListener('dragleave', function (ev) {
        ev.preventDefault();
        if (!ui.dropzone.contains(ev.relatedTarget)) ui.dropzone.classList.remove('hot');
      });
      ui.dropzone.addEventListener('drop', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        ui.dropzone.classList.remove('hot');
        var dt = ev.dataTransfer;
        handleFiles(dt && dt.files);
      });
    }
    /* 拖错位置时不要用浏览器打开文件 */
    document.addEventListener('dragover', function (ev) { ev.preventDefault(); });
    document.addEventListener('drop', function (ev) { ev.preventDefault(); });

    if (ui.fileInput) {
      ui.fileInput.addEventListener('change', function () {
        handleFiles(ui.fileInput.files);
      });
    }
    if (ui.btnChoose) ui.btnChoose.addEventListener('click', pickFile);
    if (ui.btnTogglePreview) {
      ui.btnTogglePreview.addEventListener('click', function () {
        setPreviewOpen(!state.previewOpen);
      });
    }
    if (ui.btnToggleAll) ui.btnToggleAll.addEventListener('click', function () { toggleAll(); });
    if (ui.mapSearch) ui.mapSearch.addEventListener('input', filterAllMappings);
    if (ui.btnRun) ui.btnRun.addEventListener('click', runRestore);
    if (ui.cmpPrev) ui.cmpPrev.addEventListener('click', function () {
      if (state.compare) loadComparePage(state.compare.page - 1);
    });
    if (ui.cmpNext) ui.cmpNext.addEventListener('click', function () {
      if (state.compare) loadComparePage(state.compare.page + 1);
    });
    if (ui.btnBackDrop) ui.btnBackDrop.addEventListener('click', resetAll);
    if (ui.btnAgainTop) ui.btnAgainTop.addEventListener('click', resetAll);
    if (ui.btnAgain) ui.btnAgain.addEventListener('click', resetAll);
    if (ui.toast) ui.toast.addEventListener('click', function (ev) {
      if (ev.target === ui.toast) hideToast();
    });
    window.addEventListener('online', function () { refreshHealth(); });
    window.addEventListener('offline', function () { refreshHealth(); });
  }

  function boot() {
    grabUi();
    if (!ui.dropzone) return; // 页面结构不对时静默退出，避免整页报错
    bind();
    setStep(1, { noScroll: true });
    syncSelectionUI();
    updateMapEmpty();
    /* 顶栏状态不影响主流程，失败各自降级 */
    refreshHealth();
    refreshLicense();
    /* 首次加载先取映射；失败/404 时启用「版本较旧」降级提示 */
    loadMappings({ initial: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
