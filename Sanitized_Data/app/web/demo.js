/* 只读演示页交互（设计稿，非生产代码）
   原则：无上传、无网络、无存储、无导出；所有数据是本地预置的虚构样例。 */
(function () {
  'use strict';

  var SAMPLES = {
    contract: {
      fileName: '委托代理合同_演示.pdf',
      meta: '12 页 · 1.8 MB · 演示文件',
      docTitle: '委托代理合同',
      count: '8 类 / 14 处',
      paras: [
        '甲方委托乙方代理本案一审诉讼，乙方指派律师承办。',
        '本合同项下争议标的额、付款方式与送达地址以本页所列为准。'
      ],
      rows: [
        { label: '委托人（甲方）', value: '张三', pseudo: '当事人A', width: 36, sensitive: true },
        { label: '身份证号码', value: '5101 01XX XXXX XXXX 00', pseudo: '5101 01XX XXXX XXXX 01', width: 160, sensitive: true },
        { label: '联系地址', value: '四川省成都市XX区XX路XX号', pseudo: '某市某区某路XX号', width: 180, sensitive: true },
        { label: '联系电话', value: '138 0000 0000', pseudo: '138 **** 0000', width: 104, sensitive: true },
        { label: '受托人（乙方）', value: '某律师事务所', pseudo: '某律师事务所', width: 0, sensitive: false },
        { label: '开户银行', value: '某银行成都XX支行', pseudo: '某银行某支行', width: 132, sensitive: true },
        { label: '银行账号', value: '6222 02XX XXXX 8888', pseudo: '6222 **** **** 8888', width: 150, sensitive: true },
        { label: '争议标的额', value: '¥128,000.00', pseudo: '¥***,***.00', width: 84, sensitive: true },
        { label: '案号', value: '（2026）川01XX民初XXXX号', pseudo: '（2026）川01XX民初XXXX号', width: 0, sensitive: false }
      ],
      entities: [['姓名', 2], ['证件号', 1], ['地址', 1], ['电话', 1], ['银行账号', 1], ['金额', 1], ['机构名', 1], ['案号', 1]]
    },
    medical: {
      fileName: '住院病历_演示.pdf',
      meta: '8 页 · 1.2 MB · 演示文件',
      docTitle: '入院记录（演示）',
      count: '7 类 / 11 处',
      paras: [
        '患者因“反复胸闷 3 天”入院，入院后完善相关检查。',
        '本页仅演示文档格式与脱敏位置，不构成任何诊疗建议。'
      ],
      rows: [
        { label: '患者姓名', value: '李四', pseudo: '患者A', width: 34, sensitive: true },
        { label: '身份证号码', value: '5101 02XX XXXX XXXX 00', pseudo: '5101 02XX XXXX XXXX 01', width: 160, sensitive: true },
        { label: '病历号', value: 'MR-2026-XXXX', pseudo: 'MR-****-XXXX', width: 116, sensitive: true },
        { label: '住院号', value: 'ZY-2026-XXXX', pseudo: 'ZY-****-XXXX', width: 116, sensitive: true },
        { label: '联系电话', value: '139 0000 0000', pseudo: '139 **** 0000', width: 104, sensitive: true },
        { label: '家庭住址', value: '四川省XX市XX区XX街XX号', pseudo: '某市某区某街XX号', width: 174, sensitive: true },
        { label: '入院科室', value: '心内科（演示）', pseudo: '心内科（演示）', width: 0, sensitive: false },
        { label: '入院日期', value: '2026-XX-XX', pseudo: '2026-XX-XX', width: 0, sensitive: false }
      ],
      entities: [['姓名', 1], ['证件号', 1], ['病历号', 1], ['住院号', 1], ['电话', 1], ['地址', 1], ['日期', 1]]
    },
    bank: {
      fileName: '银行对账单_演示.xlsx',
      meta: '1 个工作表 · 46 行 · 演示文件',
      docTitle: '银行流水对账单（演示）',
      count: '6 类 / 9 处',
      paras: [
        '账户交易明细，含户名、账号、开户行、交易对手与金额。',
        '本页只演示表格类文件的脱敏位置，金额与公式在真实产品中会按数值写回。'
      ],
      rows: [
        { label: '户名', value: '王五', pseudo: '客户A', width: 34, sensitive: true },
        { label: '银行账号', value: '6222 03XX XXXX 6666', pseudo: '6222 **** **** 6666', width: 150, sensitive: true },
        { label: '开户行', value: '某银行XX支行', pseudo: '某银行某支行', width: 122, sensitive: true },
        { label: '交易日期', value: '2026-XX-XX', pseudo: '2026-XX-XX', width: 0, sensitive: false },
        { label: '交易对手', value: '某建筑有限公司', pseudo: '某公司A', width: 120, sensitive: true },
        { label: '摘要', value: '工程款（演示）', pseudo: '工程款（演示）', width: 0, sensitive: false },
        { label: '交易金额', value: '¥86,500.00', pseudo: '¥**,***.00', width: 82, sensitive: true },
        { label: '余额', value: '¥1,024,300.00', pseudo: '¥*,***,***.00', width: 94, sensitive: true }
      ],
      entities: [['姓名', 1], ['银行账号', 1], ['开户行', 1], ['交易对手', 1], ['金额', 2], ['日期', 1]]
    }
  };

  var state = { sample: 'contract', mode: 'ai' };

  var $ = function (id) { return document.getElementById(id); };
  var shell = $('demoShell');
  var fileName = $('fileName');
  var fileMeta = $('fileMeta');
  var originalBody = $('originalBody');
  var outputBody = $('outputBody');
  var outputLabel = $('outputLabel');
  var outputTag = $('outputTag');
  var outputStamp = $('outputStamp');
  var entityList = $('entityList');
  var entityCount = $('entityCount');
  var toggleEntities = $('toggleEntities');
  var exportBtn = $('exportBtn');
  var readModeBtn = $('readModeBtn');
  var modal = $('exportModal');
  var modalTitle = $('modalTitle');
  var modalBody = modal.querySelector('.modal-card p');
  var lastFocus = null;

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function docHtml(sample, kind) {
    var html = '<div class="doc-title">' + esc(sample.docTitle) + '</div>';
    html += '<div class="doc-sub">演示文件 · 全部信息为虚构</div>';
    sample.rows.forEach(function (row) {
      var value;
      if (!row.sensitive) {
        value = esc(row.value);
      } else if (kind === 'original') {
        value = '<span class="mark">' + esc(row.value) + '</span>';
      } else if (state.mode === 'ai') {
        value = '<span class="pseudo">' + esc(row.pseudo) + '</span>';
      } else {
        value = '<span class="blackbar" style="width:' + row.width + 'px" aria-label="已涂黑"></span>';
      }
      html += '<div class="doc-row"><span class="lab">' + esc(row.label) + '</span><span class="val">' + value + '</span></div>';
    });
    html += '<div class="doc-para">' + sample.paras.map(esc).join(' ') + '</div>';
    return html;
  }

  function render() {
    var sample = SAMPLES[state.sample];
    fileName.textContent = sample.fileName;
    fileMeta.textContent = sample.meta;
    originalBody.innerHTML = docHtml(sample, 'original');
    outputBody.innerHTML = docHtml(sample, 'output');
    entityCount.textContent = sample.count;
    entityList.innerHTML = sample.entities.map(function (item) {
      return '<span class="entity-chip">' + esc(item[0]) + ' <b>×' + item[1] + '</b></span>';
    }).join('');

    var isAi = state.mode === 'ai';
    outputLabel.textContent = isAi ? '换名版 · 给 AI 读' : '涂黑版 · 给人看';
    outputTag.textContent = isAi ? '换名版 · 给 AI 读' : '涂黑版 · 给人看';
    outputStamp.textContent = isAi ? '可 还 原' : '不 可 逆';
    shell.setAttribute('data-mode', state.mode);

    document.querySelectorAll('[data-sample]').forEach(function (btn) {
      var on = btn.getAttribute('data-sample') === state.sample;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    document.querySelectorAll('[data-mode]').forEach(function (btn) {
      var on = btn.getAttribute('data-mode') === state.mode;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function openModal(kind) {
    lastFocus = document.activeElement;
    if (kind === 'export') {
      modalTitle.textContent = '演示页不提供导出';
      modalBody.textContent = '这是只读演示，不会生成、下载或保存文件。真实产品在您自己的电脑上完成导出，文件不上传。';
    } else {
      modalTitle.textContent = '这是只读演示';
      modalBody.textContent = '样例是预置的虚构文件；本页没有上传、没有真实处理、没有导出。真实产品请下载桌面版，在本机处理自己的文件。';
    }
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    var closeBtn = modal.querySelector('.modal-close');
    if (closeBtn) closeBtn.focus();
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  document.querySelectorAll('[data-sample]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      state.sample = btn.getAttribute('data-sample');
      render();
    });
  });

  document.querySelectorAll('[data-mode]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      state.mode = btn.getAttribute('data-mode');
      render();
    });
  });

  toggleEntities.addEventListener('click', function () {
    var collapsed = entityList.classList.toggle('collapsed');
    toggleEntities.textContent = collapsed ? '展开' : '收起';
    toggleEntities.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  });

  exportBtn.addEventListener('click', function () { openModal('export'); });
  readModeBtn.addEventListener('click', function () { openModal('read'); });

  modal.querySelectorAll('[data-close]').forEach(function (el) {
    el.addEventListener('click', closeModal);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !modal.hidden) closeModal();
  });

  render();
})();


/* ============================================================
   v2 · 动效增强（设计稿，非生产代码）
   无网络、无上传、无导出、无存储；只操作 DOM/CSS。
   ============================================================ */
(function () {
  'use strict';

  document.documentElement.classList.add('js-v2');

  var shell = document.getElementById('demoShell');
  var demoSection = document.getElementById('demo');
  var scanPct = document.getElementById('scanPct');
  var progress = document.getElementById('scanProgress');
  var replayBtn = document.getElementById('replayBtn');
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var raf = null;
  var timer = null;
  var played = false;

  function setProgress(value) {
    if (progress) progress.style.width = value + '%';
  }

  function setPct(value) {
    if (scanPct) scanPct.textContent = Math.round(value) + '%';
  }

  function markEntities() {
    var chips = document.querySelectorAll('.entity-chip');
    for (var i = 0; i < chips.length; i++) {
      chips[i].style.setProperty('--i', String(i));
    }
  }

  function finishScan() {
    if (!shell) return;
    shell.classList.remove('is-scanning');
    shell.classList.add('is-ready');
    setProgress(100);
    setPct(100);
    markEntities();
  }

  function playScan() {
    if (!shell) return;
    if (reduced) {
      finishScan();
      return;
    }
    if (raf) window.cancelAnimationFrame(raf);
    if (timer) window.clearTimeout(timer);
    shell.classList.remove('is-ready');
    shell.classList.remove('is-scanning');
    void shell.offsetWidth;
    shell.classList.add('is-scanning');
    setProgress(0);
    setPct(0);
    var start = performance.now();
    function tick(now) {
      var p = Math.min(100, ((now - start) / 1500) * 100);
      setProgress(p);
      setPct(p);
      if (p < 100) raf = window.requestAnimationFrame(tick);
    }
    raf = window.requestAnimationFrame(tick);
    timer = window.setTimeout(finishScan, 1620);
  }

  function playWipe() {
    if (!shell) return;
    shell.classList.remove('is-ready');
    void shell.offsetWidth;
    shell.classList.add('is-ready');
    markEntities();
  }

  document.querySelectorAll('[data-sample]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      window.setTimeout(playScan, 20);
    });
  });

  document.querySelectorAll('[data-mode]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      window.setTimeout(playWipe, 20);
    });
  });

  if (replayBtn) replayBtn.addEventListener('click', playScan);

  // 演示区进入视口时播放一次；之后由「重播扫描」触发
  if (demoSection && 'IntersectionObserver' in window) {
    var demoObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting && !played) {
          played = true;
          window.setTimeout(playScan, 260);
        }
      });
    }, { threshold: 0.18 });
    demoObs.observe(demoSection);
  } else {
    window.setTimeout(playScan, 500);
  }

  // 页面滚动进入
  var revealEls = document.querySelectorAll('[data-reveal]');
  if ('IntersectionObserver' in window) {
    var revealObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          revealObs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    revealEls.forEach(function (el) { revealObs.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add('is-visible'); });
  }

  // 演示舱的鼠标光斑（桌面指针）
  if (shell && window.matchMedia && window.matchMedia('(pointer: fine)').matches) {
    shell.addEventListener('pointermove', function (event) {
      var rect = shell.getBoundingClientRect();
      var mx = ((event.clientX - rect.left) / rect.width) * 100;
      var my = ((event.clientY - rect.top) / rect.height) * 100;
      shell.style.setProperty('--mx', mx.toFixed(2) + '%');
      shell.style.setProperty('--my', my.toFixed(2) + '%');
    }, { passive: true });
  }

  // Hero 右侧卡片轻微跟随倾斜
  var heroNote = document.querySelector('.hero-note');
  if (heroNote && window.matchMedia && window.matchMedia('(pointer: fine)').matches && !reduced) {
    heroNote.addEventListener('pointermove', function (event) {
      var rect = heroNote.getBoundingClientRect();
      var px = (event.clientX - rect.left) / rect.width - 0.5;
      var py = (event.clientY - rect.top) / rect.height - 0.5;
      heroNote.style.transform = 'perspective(900px) rotateX(' + (py * -4).toFixed(2) + 'deg) rotateY(' + (px * 5).toFixed(2) + 'deg)';
    }, { passive: true });
    heroNote.addEventListener('pointerleave', function () {
      heroNote.style.transform = '';
    });
  }

  markEntities();
})();
