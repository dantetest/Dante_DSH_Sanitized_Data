/* 订单页逻辑：轮询订单状态 → 已支付显示授权码；支持手工输入订单号/令牌查询 */
"use strict";

(function () {
  var state = {
    orderNo: "",
    token: "",
    order: null,
    pollTimer: null,
    tickTimer: null,
    polling: false
  };

  var el = {
    entryPanel: YZ.$("#entry-panel"),
    entryOrderNo: YZ.$("#entry-order-no"),
    entryToken: YZ.$("#entry-token"),
    entrySubmit: YZ.$("#entry-submit"),
    entryError: YZ.$("#entry-error"),
    statusPanel: YZ.$("#status-panel"),
    statusPill: YZ.$("#status-pill"),
    orderNo: YZ.$("#order-no"),
    orderAmount: YZ.$("#order-amount"),
    orderCountdown: YZ.$("#order-countdown"),
    countdownRow: YZ.$("#countdown-row"),
    paidAtRow: YZ.$("#paid-at-row"),
    paidAt: YZ.$("#paid-at"),
    statusNotice: YZ.$("#status-notice"),
    buyAgain: YZ.$("#buy-again"),
    licensePanel: YZ.$("#license-panel"),
    licenseText: YZ.$("#license-text"),
    copyLicense: YZ.$("#copy-license"),
    downloadLicense: YZ.$("#download-license")
  };

  function stopTimers() {
    state.polling = false;
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (state.tickTimer) {
      clearInterval(state.tickTimer);
      state.tickTimer = null;
    }
  }

  function setStatus(status) {
    var labels = { pending: "等待支付", paid: "已支付", expired: "已过期", closed: "已关闭" };
    YZ.setText(el.statusPill, labels[status] || status || "未知");
    el.statusPill.className = "status-pill " + (status || "");
  }

  function updateCountdown() {
    if (state.order) {
      YZ.setText(el.orderCountdown, YZ.countdownText(state.order.expires_at));
    }
  }

  function renderOrder(order) {
    state.order = order;
    YZ.setText(el.orderNo, order.order_no);
    YZ.setText(el.orderAmount, YZ.money(order.amount_cents));
    setStatus(order.status);
    YZ.show(el.statusPanel);
    YZ.show(el.entryPanel);
    YZ.hide(el.buyAgain);
    YZ.hide(el.licensePanel);

    if (order.status === "paid") {
      stopTimers();
      YZ.hide(el.countdownRow);
      if (order.paid_at) {
        YZ.setText(el.paidAt, order.paid_at);
        YZ.show(el.paidAtRow);
      }
      YZ.setNotice(el.statusNotice, "支付成功，授权已签发。", "ok");
      if (order.license && order.license.text) {
        el.licenseText.value = order.license.text;
        YZ.show(el.licensePanel);
      }
      return;
    }

    if (order.status === "expired" || order.status === "closed") {
      stopTimers();
      YZ.hide(el.countdownRow);
      YZ.setNotice(el.statusNotice, "订单已过期，请重新下单。", "warn");
      YZ.show(el.buyAgain);
      return;
    }

    YZ.show(el.countdownRow);
    updateCountdown();
    if (!state.tickTimer) {
      state.tickTimer = setInterval(updateCountdown, 1000);
    }
    YZ.setNotice(el.statusNotice,
      "等待支付…（本页每 2 秒自动查询；支付成功后立即显示授权码）", "info");
    schedulePoll(2000);
  }

  function schedulePoll(delay) {
    if (state.polling) {
      return;
    }
    state.polling = true;
    state.pollTimer = setTimeout(function () {
      state.polling = false;
      fetchOrder();
    }, delay);
  }

  function fetchOrder() {
    if (!state.orderNo || !state.token) {
      return;
    }
    var url = "/api/orders/" + encodeURIComponent(state.orderNo) +
      "?token=" + encodeURIComponent(state.token);
    YZ.apiFetch(url).then(function (order) {
      if (order.status === "pending") {
        state.order = order;
        renderOrder(order);
      } else {
        renderOrder(order);
      }
    }).catch(function (err) {
      stopTimers();
      if (err.code === "order_not_found") {
        YZ.setNotice(el.statusNotice, "订单不存在或链接已失效（查询令牌错误）。", "err");
        YZ.show(el.entryPanel);
      } else {
        YZ.setNotice(el.statusNotice, "查询失败：" + err.message, "err");
      }
    });
  }

  function showEntry() {
    YZ.show(el.entryPanel);
    YZ.hide(el.statusPanel);
    YZ.hide(el.licensePanel);
    el.entryOrderNo.value = YZ.query("order_no");
    el.entryToken.value = YZ.query("token");
    if (el.entryOrderNo.value) {
      el.entryOrderNo.focus();
    }
  }

  function submitEntry() {
    var orderNo = (el.entryOrderNo.value || "").trim();
    var token = (el.entryToken.value || "").trim();
    if (!orderNo || !token) {
      YZ.setNotice(el.entryError, "请填写订单号和查询令牌。", "err");
      return;
    }
    state.orderNo = orderNo;
    state.token = token;
    YZ.setNotice(el.entryError, "", "err");
    if (history.replaceState) {
      history.replaceState(null, "", "/order.html?order_no=" +
        encodeURIComponent(orderNo) + "&token=" + encodeURIComponent(token));
    }
    fetchOrder();
  }

  function init() {
    state.orderNo = YZ.query("order_no") || YZ.query("orderNo") || "";
    state.token = YZ.query("token") || "";
    el.entrySubmit.addEventListener("click", submitEntry);
    el.copyLicense.addEventListener("click", function () {
      YZ.copyText(el.licenseText.value).then(function () {
        YZ.setText(el.copyLicense, "已复制");
        setTimeout(function () { YZ.setText(el.copyLicense, "复制授权码"); }, 1500);
      }).catch(function () {
        el.licenseText.select();
      });
    });
    el.downloadLicense.addEventListener("click", function () {
      var name = state.orderNo || "license";
      YZ.downloadText(name + ".license.json", el.licenseText.value);
    });
    if (state.orderNo && state.token) {
      fetchOrder();
    } else {
      showEntry();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
