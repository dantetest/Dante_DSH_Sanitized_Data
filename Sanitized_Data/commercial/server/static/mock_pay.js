/* 演示支付页逻辑：查询订单 → POST /api/mock/pay → 展示授权码 */
"use strict";

(function () {
  var state = { orderNo: "", token: "", order: null };

  var el = {
    orderNo: YZ.$("#order-no"),
    payAmount: YZ.$("#pay-amount"),
    orderStatus: YZ.$("#order-status"),
    mockPayBtn: YZ.$("#mock-pay-btn"),
    payNotice: YZ.$("#pay-notice"),
    donePanel: YZ.$("#done-panel"),
    licenseText: YZ.$("#license-text"),
    copyLicense: YZ.$("#copy-license"),
    downloadLicense: YZ.$("#download-license"),
    backOrder: YZ.$("#back-order"),
    backOrderDone: YZ.$("#back-order-done"),
    pageError: YZ.$("#page-error"),
    summaryPanel: YZ.$("#summary-panel")
  };

  function orderUrl() {
    return "/order.html?order_no=" + encodeURIComponent(state.orderNo) +
      "&token=" + encodeURIComponent(state.token);
  }

  function setNotice(message, kind) {
    YZ.setNotice(el.payNotice, message, kind || "info");
  }

  function showDone(order) {
    var license = order.license || {};
    if (license.text) {
      el.licenseText.value = license.text;
    }
    YZ.hide(el.summaryPanel);
    YZ.show(el.donePanel);
  }

  function renderOrder(order) {
    state.order = order;
    YZ.setText(el.orderNo, order.order_no);
    YZ.setText(el.payAmount, YZ.money(order.amount_cents));
    YZ.setText(el.orderStatus, YZ.statusLabel(order.status));
    if (order.status === "paid") {
      showDone(order);
      return;
    }
    YZ.show(el.summaryPanel);
    if (order.status === "expired" || order.status === "closed") {
      el.mockPayBtn.disabled = true;
      setNotice("订单已过期或已关闭，无法模拟支付，请重新下单。", "warn");
      return;
    }
    el.mockPayBtn.disabled = false;
    setNotice("点击下方按钮完成演示支付。", "info");
  }

  function loadOrder() {
    var url = "/api/orders/" + encodeURIComponent(state.orderNo) +
      "?token=" + encodeURIComponent(state.token);
    YZ.apiFetch(url).then(function (order) {
      renderOrder(order);
    }).catch(function (err) {
      el.mockPayBtn.disabled = true;
      if (err.code === "order_not_found") {
        YZ.setNotice(el.pageError, "订单不存在或链接已失效（查询令牌错误）。", "err");
      } else {
        YZ.setNotice(el.pageError, "查询订单失败：" + err.message, "err");
      }
    });
  }

  function doMockPay() {
    el.mockPayBtn.disabled = true;
    setNotice("正在模拟支付…", "info");
    YZ.postJson("/api/mock/pay", {
      order_no: state.orderNo,
      token: state.token
    }).then(function (order) {
      if (order.status === "paid") {
        showDone(order);
      } else {
        el.mockPayBtn.disabled = false;
        setNotice("模拟支付未完成，请重试。", "warn");
      }
    }).catch(function (err) {
      el.mockPayBtn.disabled = false;
      setNotice("模拟支付失败：" + err.message, "err");
    });
  }

  function init() {
    state.orderNo = YZ.query("order_no") || YZ.query("orderNo") || "";
    state.token = YZ.query("token") || "";
    el.backOrder.setAttribute("href", orderUrl());
    el.backOrderDone.setAttribute("href", orderUrl());
    el.mockPayBtn.addEventListener("click", doMockPay);
    el.copyLicense.addEventListener("click", function () {
      YZ.copyText(el.licenseText.value).then(function () {
        YZ.setText(el.copyLicense, "已复制");
        setTimeout(function () { YZ.setText(el.copyLicense, "复制授权码"); }, 1500);
      }).catch(function () {
        el.licenseText.select();
      });
    });
    el.downloadLicense.addEventListener("click", function () {
      YZ.downloadText((state.orderNo || "license") + ".license.json", el.licenseText.value);
    });
    if (!state.orderNo || !state.token) {
      el.mockPayBtn.disabled = true;
      YZ.setNotice(el.pageError, "链接缺少订单号或查询令牌，请从订单页进入。", "err");
      return;
    }
    loadOrder();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
