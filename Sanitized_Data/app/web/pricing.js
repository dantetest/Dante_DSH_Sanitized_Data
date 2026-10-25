/* 价格页：购买按钮只读取本机 status 的 purchase_url；未配置就回到授权页，不伪造外链 */
(async () => {
  const note = document.getElementById('buyNote');
  let status = null;
  try {
    const res = await fetch('/api/license/status', { cache: 'no-store' });
    if (res.ok) status = await res.json();
  } catch (err) { /* 离线打开价格页时保持静态 */ }
  const purchase = (status && status.purchase_url) || '';
  if (/^https?:\/\//i.test(purchase)) {
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
    if (note) note.textContent = '购买将在系统浏览器打开官网；本机文件不会上传。价格与活动以官网为准。';
  } else if (note) {
    note.textContent = '购买入口尚未配置；可先在授权页导入已有授权，或联系交付顾问人工开通。价格与活动以官网为准。';
  }
})();
