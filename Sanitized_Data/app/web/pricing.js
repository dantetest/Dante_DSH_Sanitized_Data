/* 价格页：商业服务已配置时，购买按钮进入本机扫码升级页（/purchase?sku=...）；
   否则保持旧逻辑：只读本机 status 的 purchase_url，未配置就回到授权页，不伪造外链 */
(async () => {
  const note = document.getElementById('buyNote');
  let status = null;
  try {
    const res = await fetch('/api/license/status', { cache: 'no-store' });
    if (res.ok) status = await res.json();
  } catch (err) { /* 离线打开价格页时保持静态 */ }
  const commercial = String((status && status.commercial_url) || '').trim();
  const purchase = String((status && status.purchase_url) || '').trim();
  if (/^https?:\/\//i.test(commercial)) {
    // L3：本机购买页内支付宝扫码 → 支付成功后自动导入授权并解锁。
    // team/org 等不支持线上自助的版本，由购买页给人工联系入口。
    document.querySelectorAll('a.buy').forEach((a) => {
      const sku = a.dataset.sku || 'personal';
      a.href = '/purchase?sku=' + encodeURIComponent(sku);
      a.removeAttribute('target');
      a.removeAttribute('rel');
    });
    if (note) {
      note.textContent = '支持支付宝扫码购买：支付成功后自动导入授权并解锁，不需要手动复制授权码；'
        + '价格与活动以商业服务实时返回为准。';
    }
  } else if (/^https?:\/\//i.test(purchase)) {
    document.querySelectorAll('a.buy').forEach((a) => {
      const sku = a.dataset.sku || 'personal';
      const qs = new URLSearchParams({
        sku,
        machine_code: (status && (status.machine_display || status.machine_code)) || '',
        from: 'pricing',
      });
      a.href = purchase + (purchase.includes('?') ? '&' : '?') + qs.toString();
      a.target = '_blank';
      a.rel = 'noopener';
    });
    if (note) note.textContent = '购买将在系统浏览器打开购买页；本机文件不会上传。价格以购买页实时显示为准。';
  } else if (note) {
    note.textContent = '购买入口尚未配置；可先在授权页导入已有授权，或联系 contact@blctek.com 人工开通。价格以购买页实时显示为准。';
  }
})();
