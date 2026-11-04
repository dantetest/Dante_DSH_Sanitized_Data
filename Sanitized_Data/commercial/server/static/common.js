/* 数据隐站商业服务 · 静态页公共工具（无外部依赖） */
"use strict";

(function (global) {
  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function $all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function money(cents) {
    var value = Number(cents) / 100;
    if (!isFinite(value)) {
      return "¥--";
    }
    return "¥" + (value % 1 === 0 ? value.toFixed(0) : value.toFixed(2));
  }

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function countdownText(isoText) {
    var target = Date.parse(isoText || "");
    if (!isFinite(target)) {
      return "--:--";
    }
    var left = Math.max(0, Math.floor((target - Date.now()) / 1000));
    var minutes = Math.floor(left / 60);
    var seconds = left % 60;
    return pad(minutes) + ":" + pad(seconds);
  }

  function show(el) {
    if (el) {
      el.classList.remove("hidden");
    }
  }

  function hide(el) {
    if (el) {
      el.classList.add("hidden");
    }
  }

  function setText(el, text) {
    if (el) {
      el.textContent = text === null || text === undefined ? "" : String(text);
    }
  }

  function setNotice(el, message, kind) {
    if (!el) {
      return;
    }
    el.className = "notice " + (kind || "info");
    el.textContent = message || "";
    if (message) {
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
    }
  }

  function apiFetch(url, options) {
    var opts = options || {};
    return fetch(url, opts).then(function (response) {
      return response.json().catch(function () {
        return null;
      }).then(function (data) {
        if (!response.ok) {
          var error = (data && data.error) || {};
          var message = error.message || ("请求失败（HTTP " + response.status + "）");
          var err = new Error(message);
          err.status = response.status;
          err.code = error.code || "";
          throw err;
        }
        return data;
      });
    });
  }

  function postJson(url, body) {
    return apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  function copyText(text) {
    var value = String(text || "");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value);
    }
    return new Promise(function (resolve, reject) {
      try {
        var area = document.createElement("textarea");
        area.value = value;
        area.setAttribute("readonly", "readonly");
        area.className = "copy-area";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
        resolve();
      } catch (err) {
        reject(err);
      }
    });
  }

  function downloadText(filename, text, mime) {
    var blob = new Blob([String(text || "")], { type: mime || "application/json;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 2000);
  }

  function query(name) {
    return new URLSearchParams(location.search).get(name) || "";
  }

  function normalizeMachineCode(value) {
    var compact = String(value || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
    if (compact.length === 16) {
      return compact.slice(0, 4) + "-" + compact.slice(4, 8) + "-" +
        compact.slice(8, 12) + "-" + compact.slice(12, 16);
    }
    return String(value || "").trim().toUpperCase();
  }

  function statusLabel(status) {
    var map = { pending: "等待支付", paid: "已支付", expired: "已过期", closed: "已关闭" };
    return map[status] || status || "未知";
  }

  global.YZ = {
    $: $,
    $all: $all,
    esc: esc,
    money: money,
    countdownText: countdownText,
    show: show,
    hide: hide,
    setText: setText,
    setNotice: setNotice,
    apiFetch: apiFetch,
    postJson: postJson,
    copyText: copyText,
    downloadText: downloadText,
    query: query,
    normalizeMachineCode: normalizeMachineCode,
    statusLabel: statusLabel
  };
})(window);
