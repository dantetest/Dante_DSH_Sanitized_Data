(function(){
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function swap(fake, real){
    return '<span class="swap" data-fake="' + fake + '" data-real="' + real + '">' + fake + '</span>';
  }

  var scenes = {
    contract: {
      id: 'contract',
      label: '合同审查',
      tab: '01',
      title: '合同审查',
      file: '委托代理合同',
      prompt: '请审这份合同，列出付款、违约、管辖 3 个风险点，并给修改建议。',
      original: [
        ['委托人', '<span class="hl">张三</span>'],
        ['身份证号', '<span class="hl">5101…0012</span>'],
        ['联系电话', '<span class="hl">138…0000</span>'],
        ['受托人', '<span class="hl">四川某某律师事务所</span>'],
        ['标的额', '¥128,000.00'],
        ['付款节点', '2026-12-31 前']
      ],
      renamed: [
        ['委托人', '<span class="fake">甲方A</span>'],
        ['身份证号', '<span class="fake">ID-A</span>'],
        ['联系电话', '<span class="fake">PHONE-A</span>'],
        ['受托人', '<span class="fake">乙方B</span>'],
        ['标的额', '¥128,000.00'],
        ['付款节点', '2026-12-31 前']
      ],
      black: [
        ['委托人', 48], ['身份证号', 108], ['联系电话', 84], ['受托人', 130],
        ['标的额', 86], ['付款节点', '2026-12-31 前']
      ],
      mapping: [['张三','甲方A'],['四川某某律师事务所','乙方B'],['5101…0012','ID-A'],['138…0000','PHONE-A']],
      success: [
        ['风险 1｜付款节点', swap('甲方A','张三') + ' 应在 2026-12-31 前向 ' + swap('乙方B','四川某某律师事务所') + ' 支付 <b>128,000 元</b>；逾期违约金仅按日万分之五，低于同类合同常见水平，建议提高并写明起算日。'],
        ['风险 2｜违约责任', '合同只约定 ' + swap('甲方A','张三') + ' 逾期付款的责任，未约定 ' + swap('乙方B','四川某某律师事务所') + ' 的服务不达标责任，建议补充对等条款。'],
        ['风险 3｜管辖', '约定由“合同签订地”法院管辖，但未写明签订地；建议明确为 ' + swap('乙方B','四川某某律师事务所') + ' 所在地或 ' + swap('甲方A','张三') + ' 所在地。']
      ],
      fail: [
        '付款主体被涂黑：无法确认甲方是谁',
        '标的额被涂黑：无法计算或比较金额',
        '受托人被涂黑：无法判断责任关系',
      ],
      failTip: '只能给通用建议：请补充合同主体、金额和付款节点。',
      note: '换名版：AI 能审条款；涂黑版：AI 只看到黑块，无法判断主体与金额。'
    },
    medical: {
      id: 'medical',
      label: '病历摘要',
      tab: '02',
      title: '病历摘要',
      file: '门诊病历',
      prompt: '请提取主诉、诊断、用药，生成 200 字摘要，并提示用药冲突。',
      original: [
        ['患者', '<span class="hl">李四</span>'],
        ['病历号', '<span class="hl">BL-2026-0317</span>'],
        ['主诉', '反复头晕 2 周'],
        ['诊断', '高血压 2 级（中危）'],
        ['用药', '氨氯地平 5mg 每日一次'],
        ['复诊', '2026-12-01']
      ],
      renamed: [
        ['患者', '<span class="fake">患者A</span>'],
        ['病历号', '<span class="fake">病历号A</span>'],
        ['主诉', '反复头晕 2 周'],
        ['诊断', '高血压 2 级（中危）'],
        ['用药', '氨氯地平 5mg 每日一次'],
        ['复诊', '2026-12-01']
      ],
      black: [
        ['患者', 42], ['病历号', 104], ['主诉', '反复头晕 2 周'], ['诊断', '高血压 2 级（中危）'],
        ['用药', '氨氯地平 5mg'], ['复诊', '2026-12-01']
      ],
      mapping: [['李四','患者A'],['BL-2026-0317','病历号A'],['复诊日期','日期A']],
      success: [
        ['摘要', swap('患者A','李四') + '，病历号 ' + swap('病历号A','BL-2026-0317') + '，主诉反复头晕 2 周，诊断高血压 2 级（中危）。目前氨氯地平 5mg 每日一次，血压控制情况需结合家庭监测。'],
        ['用药提醒', '氨氯地平常见踝部水肿、面部潮红；如出现明显低血压或晕厥，建议复诊调整。'],
        ['下一步', '建议补充 7 天家庭血压记录，复诊时评估是否联合用药；保持低盐饮食。']
      ],
      fail: [
        '患者姓名被涂黑：无法确认摘要属于谁',
        '病历号被涂黑：无法关联复诊记录',
        '主诉与诊断仍可见，但无法完成患者级摘要',
        '只能给通用高血压建议，不能生成可回填的病历摘要'
      ],
      note: '换名版：AI 能生成患者级摘要；涂黑版：无法关联患者身份。'
    },
    finance: {
      id: 'finance',
      label: '财务对账',
      tab: '03',
      title: '财务对账',
      file: '银行流水对账单',
      prompt: '请找出 3 笔未匹配流水，说明金额、日期和可能原因。',
      original: [
        ['公司', '<span class="hl">成都某某商贸有限公司</span>'],
        ['账号', '<span class="hl">6222…8888</span>'],
        ['开户行', '<span class="hl">某银行成都分行</span>'],
        ['流水 1', '2026-11-03 · ¥12,800 · 货款'],
        ['流水 2', '2026-11-05 · ¥8,600 · 退款'],
        ['流水 3', '2026-11-06 · ¥45,000 · 转账']
      ],
      renamed: [
        ['公司', '<span class="fake">公司A</span>'],
        ['账号', '<span class="fake">账号A</span>'],
        ['开户行', '<span class="fake">银行A</span>'],
        ['流水 1', '2026-11-03 · ¥12,800 · 货款'],
        ['流水 2', '2026-11-05 · ¥8,600 · 退款'],
        ['流水 3', '2026-11-06 · ¥45,000 · 转账']
      ],
      black: [
        ['公司', 120], ['账号', 96], ['开户行', 88], ['流水 1', '2026-11-03 · ¥12,800'],
        ['流水 2', '2026-11-05 · ¥8,600'], ['流水 3', '2026-11-06 · ¥45,000']
      ],
      mapping: [['成都某某商贸有限公司','公司A'],['6222…8888','账号A'],['某银行成都分行','银行A']],
      success: [
        ['差异 1｜11-03', swap('公司A','成都某某商贸有限公司') + ' 收到 ¥12,800，摘要“货款”，但订单系统无对应应收单；建议核对线下合同。'],
        ['差异 2｜11-05', swap('公司A','成都某某商贸有限公司') + ' 支出 ¥8,600，摘要“退款”，但退款单号为空；建议补录原始退款单。'],
        ['差异 3｜11-06', '转出 ¥45,000 到 ' + swap('公司A','成都某某商贸有限公司') + ' 同名账户，摘要“转账”；建议确认是否内部调拨，避免重复记账。']
      ],
      fail: [
        '公司名被涂黑：无法确认流水属于哪家公司',
        '账号被涂黑：无法与银行账户匹配',
        '开户行被涂黑：无法判断跨行/同行',
        '只能按金额做通用分类，不能完成对账'
      ],
      note: '换名版：AI 能对账；涂黑版：AI 无法确认主体和账户。'
    }
  };

  var current = 'contract';
  var restoreOn = false;
  var scanTimer = null;

  function $(id){ return document.getElementById(id); }

  function docHtml(scene, kind){
    var rows = kind === 'original' ? scene.original : kind === 'renamed' ? scene.renamed : scene.black;
    var tag = kind === 'original' ? '<span class="tag">原件</span><span>只在本机</span><span class="spacer"></span><span>真实值</span>'
      : kind === 'renamed' ? '<span class="tag gold">换名版</span><span>给 AI 读</span><span class="spacer"></span><span class="tag red">可本地还原</span>'
      : '<span class="tag red">涂黑版</span><span>给人看</span><span class="spacer"></span><span class="tag red">不可还原</span>';
    var title = kind === 'original' ? '原始文件' : kind === 'renamed' ? 'AI 视角' : '可外发版';
    var paperRows = rows.map(function(r){
      var v = (typeof r[1] === 'number') ? '<span class="black" style="width:' + r[1] + 'px"></span>' : r[1];
      return '<div class="row"><span class="k">' + r[0] + '</span><span class="v">' + v + '</span></div>';
    }).join('');
    var note = kind === 'original' ? 'AI 看不到这一页。' : kind === 'renamed' ? '金额、日期、条款结构保留；AI 只读甲方A/乙方B，真名只在本机。' : '真删除、不可还原；给人看/外发。';
    var stamp = kind === 'renamed' ? '<div class="stamp-sm">可 还 原</div>' : kind === 'black' ? '<div class="stamp-sm">已 隐 去</div>' : '';
    return '<section class="doc ' + (kind === 'black' ? 'blackdoc' : kind) + '"><div class="ph">' + tag + '</div><div class="paper"><h3>' + scene.file + '</h3>' + paperRows + stamp + '<div class="note">' + note + '</div></div></section>';
  }

  function renderMapping(scene){
    $('mappingPairs').innerHTML = scene.mapping.map(function(p){
      return '<div class="pair" data-fake="' + p[1] + '"><span>' + p[0] + '</span><span class="arr">→</span><span>' + p[1] + '</span></div>';
    }).join('');
  }

  function userMsg(text){
    return '<div class="msg user"><div class="bubble">' + text + '</div></div>';
  }

  function renderSuccess(scene){
    var risks = '<div class="risk">' + scene.success.map(function(r){
      return '<div class="n">' + r[0] + '</div><div class="t">' + r[1] + '</div>';
    }).join('') + '</div>';
    var restore = '可本地还原（AI 结果回到本机后）：' + scene.mapping.map(function(p){ return p[1] + ' → ' + p[0]; }).join('；');
    var tools = '<span class="tool ok">读取换名版</span><span class="tool ok">识别 ' + scene.mapping.length + ' 个主体</span><span class="tool ok">完成 ' + scene.success.length + ' 项分析</span>';
    $('successThread').innerHTML = userMsg(scene.prompt) +
      '<div class="msg assistant"><div class="agent-avatar">隐</div><div class="msg-main">' +
      '<div class="agent-line">数据隐站 Agent <span>只读演示 · 换名版</span></div>' +
      '<div class="toolrow">' + tools + '</div>' +
      '<div class="bubble"><p class="agent-result">已完成「' + scene.title + '」。</p>' + risks + '<div class="restore-line">' + restore + '</div></div>' +
      '<div class="msg-meta">AI 全程只读甲方A/乙方B · 预置演示 · 不调用在线模型</div>' +
      '</div></div>';
  }

  function renderFail(scene){
    var tools = '<span class="tool bad">读取涂黑版</span><span class="tool bad">主体不可见</span><span class="tool bad">金额不可见</span>';
    var list = '<ul>' + scene.fail.map(function(x){ return '<li>' + x + '</li>'; }).join('') + '</ul>';
    $('failThread').innerHTML = userMsg(scene.prompt) +
      '<div class="msg assistant"><div class="agent-avatar bad">隐</div><div class="msg-main">' +
      '<div class="agent-line">数据隐站 Agent <span class="bad">只读演示 · 读取失败</span></div>' +
      '<div class="toolrow">' + tools + '</div>' +
      '<div class="bubble"><p class="agent-result bad">无法完成同一任务。</p><p>我先读了这份涂黑版，但关键字段已被删除，没法完成同一项任务。</p>' + list + '<div class="restore-line">' + scene.failTip + '</div></div>' +
      '<div class="msg-meta">不可还原 · 涂黑版是破坏性重写</div>' +
      '</div></div>';
  }

  function updateRestore(){
    var btn = $('restoreBtn');
    btn.setAttribute('aria-checked', restoreOn ? 'true' : 'false');
    btn.querySelector('.label').textContent = restoreOn ? '已本地还原' : '本地还原真名';
    Array.prototype.forEach.call(document.querySelectorAll('#successThread .swap'), function(el){
      el.textContent = restoreOn ? el.getAttribute('data-real') : el.getAttribute('data-fake');
      el.classList.toggle('restored', restoreOn);
    });
    Array.prototype.forEach.call(document.querySelectorAll('#mappingPairs .pair'), function(el){
      el.classList.toggle('on', restoreOn);
    });
  }

  function renderScene(id, animate){
    current = id;
    var scene = scenes[id];
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function(b){ var on = b.getAttribute('data-scene') === id; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
    $('rootDoc').innerHTML = docHtml(scene,'original');
    $('renamedDoc').innerHTML = docHtml(scene,'renamed');
    $('blackDoc').innerHTML = docHtml(scene,'black');
    renderSuccess(scene);
    renderFail(scene);
    renderMapping(scene);
    restoreOn = false;
    updateRestore();
    var cards = document.querySelectorAll('.doc, .chat-card');
    Array.prototype.forEach.call(cards, function(c){ c.classList.remove('in'); void c.offsetWidth; c.classList.add('in'); });
    if (!animate) return;
    runScan();
  }

  function runScan(){
    if (reduced) return;
    var overlay = $('scan');
    var pct = $('scanPct');
    if (!overlay || !pct) return;
    if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
    overlay.classList.add('on');
    var v = 0;
    pct.textContent = 'SCANNING 00%';
    scanTimer = setInterval(function(){
      v += 7 + Math.floor(Math.random()*8);
      if (v > 100) v = 100;
      pct.textContent = 'SCANNING ' + (v < 10 ? '0' : '') + v + '%';
      if (v >= 100) {
        clearInterval(scanTimer); scanTimer = null;
        setTimeout(function(){ overlay.classList.remove('on'); }, 380);
      }
    }, 75);
  }

  document.addEventListener('DOMContentLoaded', function(){
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function(b){
      b.addEventListener('click', function(){ renderScene(b.getAttribute('data-scene'), true); });
    });
    $('restoreBtn').addEventListener('click', function(){
      restoreOn = !restoreOn; updateRestore();
    });
    $('replayBtn').addEventListener('click', function(){ runScan(); });
    $('watchWorkspace').addEventListener('click', function(){ window.location.href = '/demo/workspace'; });
    renderScene('contract', true);
  });
})();