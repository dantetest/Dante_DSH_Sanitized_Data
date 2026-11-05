/* 购买页逻辑：读目录 → 下单 → 展示服务端二维码 → 轮询 → 展示授权码 */
"use strict";

(function () {
  var FEATURE_LABELS = {
    all_packs: "全部规则包",
    custom_packs: "自定义规则包",
    tif: "TIF / 多页扫描件",
    d3d: "双层可搜索 PDF",
    mcp: "MCP 集成",
    batch: "批量处理",
    audit_export: "审计导出",
    ocr: "OCR 识别",
    team_admin: "团队管理",
    org_deploy: "机构部署"
  };
  var TERM_LABELS = { annual: "1 年", perpetual: "永久" };
  var MACHINE_CODE_RE = /^[0-9A-Z]{4}(-[0-9A-Z]{4}){3}$/;

  var state = {
    catalog: null,
    skuId: "",
    channel: "",
    order: null,
    pollTimer: null,
    tickTimer: null
  };

  var el = {
    machineCode: YZ.$("#machine-code"),
    email: YZ.$("#email"),
    skuList: YZ.$("#sku-list"),
    catalogError: YZ.$("#catalog-error"),
    channelList: YZ.$("#channel-list"),
    submitBtn: YZ.$("#submit-btn"),
    formError: YZ.$("#form-error"),
    payPanel: YZ.$("#pay-panel"),
    qrBox: YZ.$("#qr-box"),
    payAmount: YZ.$("#pay-amount"),
    payOrderNo: YZ.$("#pay-order-no"),
    payChannel: YZ.$("#pay-channel"),
    payCountdown: YZ.$("#pay-countdown"),
    payStatus: YZ.$("#pay-status"),
    payStatusPill: YZ.$("#pay-status-pill"),
    payUrlLink: YZ.$("#pay-url-link"),
    donePanel: YZ.$("#done-panel"),
    licenseText: YZ.$("#license-text"),
    copyLicense: YZ.$("#copy-license"),
    downloadLicense: YZ.$("#download-license"),
    summarySku: YZ.$("#summary-sku"),
    summaryPrice: YZ.$("#summary-price"),
    summaryTerm: YZ.$("#summary-term"),
    summaryMachine: YZ.$("#summary-machine")
  };

  function featureList(features) {
    var items = (features || []).map(function (name) {
      return "<li>" + YZ.esc(FEATURE_LABELS[name] || name) + "</li>";
    });
    return items.join("");
  }

  function renderSkus(catalog) {
    el.skuList.innerHTML = "";
    (catalog.skus || []).forEach(function (sku) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "sku";
      button.setAttribute("data-sku", sku.id);
      button.innerHTML =
        '<span class="sku-name">' + YZ.esc(sku.name) + "</span>" +
        '<div class="sku-price">' + YZ.esc(YZ.money(sku.price_cents)) + "</div>" +
        '<div class="sku-term">' + YZ.esc(TERM_LABELS[sku.term] || sku.term || "") +
        " · 可绑定 " + YZ.esc(sku.devices) + " 台设备</div>" +
        '<ul class="sku-features">' + featureList(sku.features) + "</ul>";
      button.addEventListener("click", function () {
        selectSku(sku.id);
      });
      el.skuList.appendChild(button);
    });
    if (!(catalog.skus || []).length) {
      el.skuList.innerHTML = '<p class="muted">暂无可购买版本，请联系客服。</p>';
    }
  }

  function updateSummary() {
    var sku = null;
    if (state.catalog) {
      (state.catalog.skus || []).forEach(function (item) {
        if (item.id === state.skuId) {
          sku = item;
        }
      });
    }
    if (sku) {
      YZ.setText(el.summarySku, sku.name);
      YZ.setText(el.summaryPrice, YZ.money(sku.price_cents));
      YZ.setText(el.summaryTerm, "/ " + (TERM_LABELS[sku.term] || sku.term || "年"));
    }
    var code = YZ.normalizeMachineCode(el.machineCode.value || "");
    YZ.setText(el.summaryMachine, code || "未填写");
  }

  function selectSku(skuId) {
    state.skuId = skuId;
    YZ.$all(".sku", el.skuList).forEach(function (node) {
      node.classList.toggle("selected", node.getAttribute("data-sku") === skuId);
    });
    updateSummary();
    updateSubmitState();
  }

  function renderChannels(catalog) {
    el.channelList.innerHTML = "";
    var channels = (catalog.channels || []).filter(function (channel) {
      return channel.enabled;
    });
    var firstEnabled = "";
    channels.forEach(function (channel) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "channel";
      button.setAttribute("data-channel", channel.id);
      button.textContent = channel.label;
      button.addEventListener("click", function () {
        selectChannel(channel.id);
      });
      el.channelList.appendChild(button);
      if (!firstEnabled) {
        firstEnabled = channel.id;
      }
    });
    if (!channels.length) {
      el.channelList.innerHTML = '<p class="muted small">暂无可用支付方式，请联系交付顾问。</p>';
    }
    var wanted = YZ.query("channel");
    if (wanted && channels.some(function (c) { return c.id === wanted; })) {
      selectChannel(wanted);
    } else if (firstEnabled) {
      selectChannel(firstEnabled);
    }
  }

  function selectChannel(channelId) {
    state.channel = channelId;
    YZ.$all(".channel", el.channelList).forEach(function (node) {
      node.classList.toggle("selected", node.getAttribute("data-channel") === channelId);
    });
    updateSubmitState();
  }

  function updateSubmitState() {
    el.submitBtn.disabled = !(state.catalog && state.skuId && state.channel);
  }

  function machineCodeValue() {
    var value = YZ.normalizeMachineCode(el.machineCode.value);
    el.machineCode.value = value;
    return value;
  }

  function setFormError(message) {
    YZ.setNotice(el.formError, message, "err");
  }

  function renderPay(order) {
    state.order = order;
    YZ.setText(el.payAmount, YZ.money(order.amount_cents));
    YZ.setText(el.payOrderNo, order.order_no);
    var channelLabel = "";
    (state.catalog.channels || []).forEach(function (channel) {
      if (channel.id === order.channel) {
        channelLabel = channel.label;
      }
    });
    YZ.setText(el.payChannel, channelLabel || order.channel || "");
    if (order.qr_svg) {
      el.qrBox.innerHTML = order.qr_svg; /* 只信任本商业服务返回的内联 SVG */
    }
    var payUrl = String(order.pay_url || "");
    if (/^https?:\/\//.test(payUrl)) {
      el.payUrlLink.setAttribute("href", payUrl);
      YZ.show(el.payUrlLink);
    } else {
      YZ.hide(el.payUrlLink);
    }
    YZ.show(el.payPanel);
    YZ.setText(el.payStatusPill, "等待支付");
    el.payStatusPill.className = "status-pill pending";
    YZ.setNotice(el.payStatus, "等待支付…（本页每 2 秒自动检测，付款成功后立即显示授权码）", "info");
    startTimers();
  }

  function startTimers() {
    stopTimers();
    updateCountdown();
    state.tickTimer = setInterval(updateCountdown, 1000);
    state.pollTimer = setTimeout(pollOrder, 2000);
  }

  function stopTimers() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (state.tickTimer) {
      clearInterval(state.tickTimer);
      state.tickTimer = null;
    }
  }

  function updateCountdown() {
    if (!state.order) {
      return;
    }
    YZ.setText(el.payCountdown, YZ.countdownText(state.order.expires_at));
  }

  function pollOrder() {
    var order = state.order;
    if (!order) {
      return;
    }
    var url = "/api/orders/" + encodeURIComponent(order.order_no) +
      "?token=" + encodeURIComponent(order.order_token);
    YZ.apiFetch(url).then(function (data) {
      if (data.status === "paid") {
        showDone(data);
        return;
      }
      if (data.status === "expired" || data.status === "closed") {
        stopTimers();
        YZ.setNotice(el.payStatus, "订单已过期，请重新下单。", "warn");
        el.submitBtn.disabled = false;
        return;
      }
      state.pollTimer = setTimeout(pollOrder, 2000);
    }).catch(function (err) {
      YZ.setNotice(el.payStatus, "查询订单失败：" + err.message, "err");
      state.pollTimer = setTimeout(pollOrder, 3000);
    });
  }

  function showDone(order) {
    stopTimers();
    var license = order.license || {};
    YZ.hide(el.payPanel);
    if (license.text) {
      el.licenseText.value = license.text;
    }
    YZ.show(el.donePanel);
  }

  function createOrder() {
    setFormError("");
    var machineCode = machineCodeValue();
    if (!MACHINE_CODE_RE.test(machineCode)) {
      setFormError("机器码格式无效，应为 XXXX-XXXX-XXXX-XXXX（可在软件「授权」页复制）。");
      el.machineCode.focus();
      return;
    }
    el.submitBtn.disabled = true;
    el.submitBtn.textContent = "正在创建订单…";
    var payload = {
      sku: state.skuId,
      machine_code: machineCode,
      channel: state.channel
    };
    var email = (el.email.value || "").trim();
    if (email) {
      payload.email = email;
    }
    YZ.postJson("/api/orders", payload).then(function (order) {
      el.submitBtn.textContent = "生成支付二维码";
      el.submitBtn.disabled = false;
      renderPay(order);
      el.payPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }).catch(function (err) {
      el.submitBtn.textContent = "生成支付二维码";
      el.submitBtn.disabled = false;
      setFormError(err.message || "创建订单失败，请稍后重试。");
    });
  }

  function loadCatalog() {
    YZ.apiFetch("/api/catalog").then(function (catalog) {
      state.catalog = catalog;
      renderSkus(catalog);
      renderChannels(catalog);
      updateSummary();
      updateSubmitState();
      var wanted = YZ.query("sku");
      if (wanted && catalog.skus.some(function (sku) { return sku.id === wanted; })) {
        selectSku(wanted);
      } else if (catalog.skus.length) {
        selectSku(catalog.skus[0].id);
      }
    }).catch(function (err) {
      YZ.setNotice(el.catalogError, "加载价格失败：" + err.message, "err");
      el.skuList.innerHTML = "";
    });
  }

  function init() {
    var prefill = YZ.query("machine_code");
    if (prefill) {
      el.machineCode.value = YZ.normalizeMachineCode(prefill);
    }
    el.submitBtn.addEventListener("click", createOrder);
    el.machineCode.addEventListener("input", updateSummary);
    el.machineCode.addEventListener("blur", function () {
      el.machineCode.value = machineCodeValue();
      updateSummary();
    });
    el.copyLicense.addEventListener("click", function () {
      YZ.copyText(el.licenseText.value).then(function () {
        YZ.setText(el.copyLicense, "已复制");
        setTimeout(function () { YZ.setText(el.copyLicense, "复制授权码"); }, 1500);
      }).catch(function () {
        el.licenseText.select();
        YZ.setNotice(el.payStatus, "复制失败，请手动全选复制。", "warn");
      });
    });
    el.downloadLicense.addEventListener("click", function () {
      var orderNo = (state.order && state.order.order_no) || "license";
      YZ.downloadText(orderNo + ".license.json", el.licenseText.value);
    });
    loadCatalog();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();


